# Ring-Check

Script to run rebalance over a bunch of ring files and make stats.

# Getting Started

## Create a Bundle

`ring-check-bundle /path/to/some/rings <descriptive_name_of_bundle>`

## Extract the Bundle somewhere with some CPU (it will get warm)

`ring-check-extract /path/to/rings.bundle <descriptive_name_of_bundle>`

## Get ready to log!

`source ./logs/source.me`

## Warm up the room!

`ring-check /path/to/extracted/bundle`
