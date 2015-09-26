import argparse
import collections
import datetime
import logging
import multiprocessing
from multiprocessing import queues
import os
import Queue
import sys
import time
import traceback
import threading

import swift
from swift.common.ring import RingBuilder

WORKER_COUNT = multiprocessing.cpu_count()
LOG_DIR = os.environ.get('RING_CHECK_LOGS', '.')


parser = argparse.ArgumentParser()
parser.add_argument('builder_path', help='path to builder')
parser.add_argument('-n', '--limit', type=int, default=None,
                    help='stop after limit')
parser.add_argument('-l', '--log-dir', default=LOG_DIR,
                    help='set the log dir')


class LevelFilter(object):

    def __init__(self, level, exclude=False):
        self.level = level
        self.accept = not exclude

    def filter(self, record):
        if record.levelno == self.level:
            return self.accept
        return not self.accept


def configure_logging(log_dir):
    logging.getLogger('swift.ring.builder').disabled = True
    now = datetime.datetime.now()
    log_name = 'ring-check_swift-%s_%s' % (
        swift.__version__, str(now).replace(' ', '-'))
    log_path = os.path.join(log_dir, log_name)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    stats_handler = logging.FileHandler(log_path + '.log')
    stats_handler.addFilter(LevelFilter(logging.INFO))
    root.addHandler(stats_handler)
    error_handler = logging.FileHandler(log_path + '.err')
    error_handler.addFilter(LevelFilter(logging.INFO, exclude=True))
    root.addHandler(error_handler)


def find_builders(builder_path, limit, **kwargs):
    """
    Walks builder_path to try and find things files that look like
    builder(s).

    :param builder_path: path to builder(s)
    :param limit: only yield at most limit builders
    """
    def builder_file_gen():
        if os.path.isdir(builder_path):
            for root, dirs, files in os.walk(builder_path):
                for filename in files:
                    builder_file = os.path.join(root, filename)
                    yield builder_file
        else:
            yield builder_path

    builder_count = 0
    for builder_file in builder_file_gen():
        if limit and builder_count >= limit:
            return
        name, ext = os.path.splitext(builder_file)
        if ext != '.builder':
            continue
        builder_count += 1
        yield builder_file


def check_builder(builder_file):
    """
    Create a builder from builder_file and rebalance, return the stats.

    :param builder_file: path to builder on disk

    :returns: stats, a dict, information about the check
    """
    builder = RingBuilder.load(builder_file)
    stats = {
        'builder_file': builder_file,
        'parts': builder.parts,
        'replicas': builder.replicas,
        'initial_balance': builder.get_balance(),
        'initial_dispersion': builder.dispersion,
    }
    start = time.time()
    parts_moved, final_balance = builder.rebalance()
    stats.update({
        'parts_moved': parts_moved,
        'final_balance': final_balance,
        'final_dispersion': builder.dispersion,
        'rebalance_time': time.time() - start,
    })
    return stats


class Worker(multiprocessing.Process):

    def __init__(self, q, r):
        super(Worker, self).__init__()
        self.q = q
        self.r = r

    def run(self):
        while True:
            builder_file = self.q.get()
            if builder_file is None:
                break
            success = True
            try:
                stats = check_builder(builder_file)
            except:
                success = False
                stats = {
                    'builder_file': builder_file,
                    'traceback': traceback.format_exc(),
                }
            self.r.put((success, stats))


class Feeder(threading.Thread):

    def __init__(self, options):
        super(Feeder, self).__init__()
        self.q = queues.Queue(WORKER_COUNT)
        kwargs = dict(vars(options))
        self.builder_gen = find_builders(**kwargs)
        self.running = True
        self.stats = collections.defaultdict(int)

    def run(self):
        try:
            for builder_file in self.builder_gen:
                if not self.running:
                    break
                self.stats['builders_found'] += 1
                self.q.put(builder_file)
        finally:
            for i in range(WORKER_COUNT):
                self.q.put(None)

    def stop(self):
        self.running = False


fields = (
    'builder_file',
    'parts',
    'replicas',
    'initial_balance',
    'final_balance',
    'initial_dispersion',
    'final_dispersion',
    'parts_moved',
    'rebalance_time',
)


def log_csv(*args):
    logging.info(', '.join([str(x) for x in args]))


def log_stats(stats):
    log_csv(*[stats.get(f, '') for f in fields])


def log_error(stats):
    logging.error('Unable to check %(builder_file)r:\n%(traceback)s', stats)


class Logger(threading.Thread):

    def __init__(self):
        super(Logger, self).__init__()
        self.q = queues.Queue()
        self.stats = collections.defaultdict(int)
        self.running = True

    def run(self):
        while True:
            try:
                response = self.q.get(True, 1)
            except Queue.Empty:
                if not self.running:
                    return
                continue
            if response:
                self.handle_response(response)
            else:
                self.running = False

    def handle_response(self, response):
        success, stats = response
        self.stats['builders_checked'] += 1
        if success:
            self.stats['builders_success'] += 1
            log_stats(stats)
        else:
            self.stats['builders_error'] += 1
            log_error(stats)

    def stop(self):
        self.q.put(None)


runtime_fields = (
    'runtime',
    'found',
    'checked',
    'success',
    'errors',
)


class HeartBeat(object):

    def __init__(self, tick):
        self.last_tick = 0
        self.ticks = 0
        self.tick = tick

    def pump(self):
        now = time.time()
        if now > self.last_tick + 1:
            self.tick(now, self.ticks)
            self.ticks += 1
            self.last_tick = time.time()


def drain(start, feeder, logger, pool):

    def tick(now, ticks):
        if not ticks % 10:
            print ' '.join('%8s' % f for f in runtime_fields)
        stats = {
            'runtime': '%0.2f' % (now - start),
            'found': feeder.stats['builders_found'],
            'checked': logger.stats['builders_checked'],
            'success': logger.stats['builders_success'],
            'errors': logger.stats['builders_error'],
        }
        print ' '.join(['%8s' % stats[f] for f in runtime_fields])

    heartbeat = HeartBeat(tick=tick)

    while feeder.isAlive():
        feeder.join(0.1)
        heartbeat.pump()
    while any(pool):
        for worker in pool:
            worker.join(0.1)
            heartbeat.pump()
        pool = [w for w in pool if w.is_alive()]
    logger.stop()
    while logger.isAlive():
        logger.join(0.1)
        heartbeat.pump()
    tick(time.time(), 0)


def main():
    options = parser.parse_args()
    configure_logging(options.log_dir)
    feeder = Feeder(options)
    logger = Logger()
    pool = [Worker(feeder.q, logger.q) for i in range(WORKER_COUNT)]
    for worker in pool:
        worker.start()
    feeder.start()
    logger.start()
    start = time.time()
    log_csv(*fields)  # headers
    while logger.isAlive():
        try:
            drain(start, feeder, logger, pool)
        except KeyboardInterrupt:
            print 'user quit...'
        finally:
            feeder.stop()

if __name__ == "__main__":
    sys.exit(main())
