#!/bin/bash

# fix buster docker.service start fail
#sudo update-alternatives --set iptables /usr/sbin/iptables-legacy

# uninstall old version
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
    sudo apt-get remove $pkg;
done

# apt repository
# Add Docker's official GPG key:
sudo apt-get update
sudo apt-get install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
echo \
	  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
	    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
	      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update

# install docker packages
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# verify installation
sudo docker run hello-world
