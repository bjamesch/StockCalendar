#!/bin/bash
touch "$HOME/.wifi_always_on"
nmcli radio wifi on
echo "Wi-Fi will now stay on, ignoring the schedule. Run wifi_follow_schedule.sh to revert."
