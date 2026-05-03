#!/bin/sh
# Write /etc/hosts entries for Fastly S3 endpoint to bypass Docker's DNS resolver
echo "151.101.41.51 us-west.object.fastlystorage.app" >> /etc/hosts
exec /opt/quickwit/quickwit run
