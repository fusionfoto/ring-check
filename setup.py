#!/usr/bin/python
# Copyright (c) 2017 SwiftStack, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software

from setuptools import setup, find_packages

setup(
    name='ring-check',
    version='0.1',
    description='Swift Ring Checker',
    author='SwiftStack',
    packages=find_packages(),
    scripts=[
        'bin/ring-check-bundle',
        'bin/ring-check-extract',
    ],
    entry_points={
        'console_scripts': [
            'ring-check = ring_check.check:main',
        ],
    },
)
