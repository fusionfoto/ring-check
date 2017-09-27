# Copyright (c) 2017 SwiftStack, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software

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

from ring_check.find import find_builders

WORKER_COUNT = multiprocessing.cpu_count()
LOG_DIR = os.environ.get('RING_CHECK_LOGS', '.')


parser = argparse.ArgumentParser()
parser.add_argument('builder_path', help='path to builder')
parser.add_argument('-n', '--limit', type=int, default=None,
                    help='stop after limit')
parser.add_argument('-l', '--log-dir', default=LOG_DIR,
                    help='set the log dir')
parser.add_argument('-s', '--save-builder', action='store_true',
                    help='Change the dataset in place')
parser.add_argument('--fix-replicas', action='store_true',
                    help='Reduce replicas to # devices if needed')
parser.add_argument('--set-overload', type=float, default=None,
                    help='set overload to a specific amount')
parser.add_argument('--set-min-part-hours', type=int, default=None,
                    help='set min-part-hours to a specific amount')


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
    stats_handler = logging.FileHandler(log_path + '.csv')
    stats_handler.addFilter(LevelFilter(logging.INFO))
    root.addHandler(stats_handler)
    error_handler = logging.FileHandler(log_path + '.err')
    error_handler.addFilter(LevelFilter(logging.INFO, exclude=True))
    root.addHandler(error_handler)


def check_builder(builder_file, save_builder=False, fix_replicas=False,
                  set_overload=None, set_min_part_hours=None, **kwargs):
    """
    Create a builder from builder_file and rebalance, return the stats.

    :param builder_file: path to builder on disk
    :param save_builder: bool, if true save builder after rebalance
    :param fix_replicas: bool, if true reduce replica count on ring to
                         max(count_of_devices_with_weight,
                             current_replica_count)
    :param set_overload: float or None, if float set_overload on the builder
                         before rebalance
    :param set_min_part_hours: int or None, if int set_min_part_hours on the
                               builder before rebalance

    :returns: stats, a dict, information about the check
    """
    builder = RingBuilder.load(builder_file)
    count_of_devices_with_weight = len([d for d in builder._iter_devs()
                                        if d['weight'] > 0])
    stats = {
        'builder_file': builder_file,
        'parts': builder.parts,
        'replicas': builder.replicas,
        'num_devs': count_of_devices_with_weight,
        'overload': builder.overload,
        'min_part_hours': builder.min_part_hours,
    }
    if count_of_devices_with_weight < 1:
        return stats
    builder._build_dispersion_graph()
    stats.update({
        'initial_balance': builder.get_balance(),
        'initial_dispersion': builder.dispersion,
    })
    if fix_replicas:
        if builder.replicas > count_of_devices_with_weight:
            builder.set_replicas(float(count_of_devices_with_weight))
    if set_overload is not None:
        builder.set_overload(set_overload)
    if set_min_part_hours is not None:
        builder.change_min_part_hours(set_min_part_hours)
    start = time.time()
    parts_moved, final_balance = builder.rebalance()[:2]
    builder.validate()
    if save_builder:
        builder.save(builder_file)
    stats.update({
        'final_replicas': builder.replicas,
        'final_overload': builder.overload,
        'final_min_part_hours': builder.min_part_hours,
        'parts_moved': parts_moved,
        'final_balance': final_balance,
        'final_dispersion': builder.dispersion,
        'rebalance_time': time.time() - start,
    })
    return stats


class Worker(multiprocessing.Process):

    def __init__(self, q, r, options):
        super(Worker, self).__init__()
        self.q = q
        self.r = r
        self.kwargs = dict(vars(options))

    def run(self):
        while True:
            builder_file = self.q.get()
            if builder_file is None:
                break
            success = True
            try:
                stats = check_builder(builder_file, **self.kwargs)
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
    'num_devs',
    'replicas',
    'final_replicas',
    'overload',
    'final_overload',
    'min_part_hours',
    'final_min_part_hours',
    'initial_balance',
    'final_balance',
    'initial_dispersion',
    'final_dispersion',
    'parts_moved',
    'rebalance_time',
)


def log_csv(*args):
    logging.info(','.join([str(x) for x in args]))


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
    pool = [Worker(feeder.q, logger.q, options) for i in range(WORKER_COUNT)]
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
