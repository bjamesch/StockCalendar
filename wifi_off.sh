#!/bin/bash
# Skip the scheduled shutoff if the always-on override is active.
if [ -f "$HOME/.wifi_always_on" ]; then
    exit 0
fi
sudo nmcli radio wifi off
