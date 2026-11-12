#!/bin/bash
# /home/hospibot/run_docker_teleop.sh

echo "--- HOSPIBOT TELEOP LAUNCHER ---"
cd /home/hospibot/Body_docker

# 0. Wait for the network
# The robot's router boots slower than the Pi. If the ROS nodes start before
# WiFi is up, CycloneDDS binds to docker0 and the nodes stay invisible to the
# head and to every other machine until they are restarted.
echo "Waiting for network..."
for i in $(seq 1 150); do
    if ip -4 route show default | grep -q .; then
        echo "Network is up."
        break
    fi
    sleep 2
done

# 1. Start Docker
echo "Starting Docker Compose..."
/usr/bin/docker compose up -d

# 2. Run the Teleop
# IMPORTANT CHANGE: We added 'source install/setup.bash' below
echo "Entering Container..."

docker exec -it hospibot_container bash -c "source /etc/turtlebot4/setup.bash && source install/setup.bash && ros2 launch body_bringup body_bringup.launch.py"

# 3. Keep window open

echo "Teleop process ended."
exec bash
