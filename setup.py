#!/usr/bin/python
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
