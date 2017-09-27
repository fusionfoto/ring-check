# Copyright (c) 2017 SwiftStack, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software

import os
import sys


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


def main():
    pass


if __name__ == "__main__":
    sys.exit(main())
