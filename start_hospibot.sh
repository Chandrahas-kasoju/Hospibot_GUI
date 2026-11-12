#!/bin/bash
# /home/hospibot/start_hospibot.sh
# Started by ~/.config/autostart/hospibot.desktop on desktop login.
#
# Only the GUI is started here. The ROS nodes are started by the Body_docker
# container itself (start_body.sh, restart: unless-stopped), so no terminal
# window or `docker exec` is needed any more.
#
# To watch the ROS output:   docker logs -f hospibot_container
# To restart the ROS nodes:  docker restart hospibot_container

# Log output for debugging
exec > /tmp/hospibot_log.txt 2>&1
echo "Script started at $(date)"

echo "Launching GUI..."
exec /usr/bin/python3 /home/hospibot/hospibot_ws/doctortrial/hospibot.py
