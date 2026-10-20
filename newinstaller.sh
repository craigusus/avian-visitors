#!/usr/bin/env bash

installer_usage() {
  echo "Usage: $0 [--educators] [--no-usb-mic] [--illustrations <URL>]" >&2
  exit 64
}
installer_args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --educators|--no-usb-mic) installer_args+=("$1") ;;
    --illustrations)
      [ "$#" -ge 2 ] && [ -n "$2" ] || installer_usage
      installer_args+=("$1" "$2")
      shift
      ;;
    *) installer_usage ;;
  esac
  shift
done

if [ "$EUID" == 0 ]
  then echo "Please run as a non-root user."
  exit
fi

if [ "$(uname -m)" != "aarch64" ] && [ "$(uname -m)" != "x86_64" ];then
  echo "BirdNET-Pi requires a 64-bit OS.
It looks like your operating system is using $(uname -m),
but would need to be aarch64."
  exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info[0]}{sys.version_info[1]}')")
if [ "${PY_VERSION}" == "39" ] ;then
  echo "### BirdNET-Pi requires a newer OS. Bullseye is deprecated, please use Bookworm. ###"
  [ -z "${FORCE_BULLSEYE}" ] && exit
fi

# we require passwordless sudo
sudo -K
if ! sudo -n true; then
    echo "Passwordless sudo is not working. Aborting"
    exit
fi

# Simple new installer
HOME=$HOME
USER=$USER

export HOME=$HOME
export USER=$USER

PACKAGES_MISSING=
for cmd in git jq ; do
  if ! which $cmd &> /dev/null;then
      PACKAGES_MISSING="${PACKAGES_MISSING} $cmd"
  fi
done
if [[ ! -z $PACKAGES_MISSING ]] ; then
  sudo apt update
  sudo apt -y install $PACKAGES_MISSING
fi

branch=avian-visitors
git clone -b $branch --depth=1 https://github.com/Twarner491/AvianVisitors.git ${HOME}/BirdNET-Pi &&

"$HOME/BirdNET-Pi/scripts/install_birdnet.sh" "${installer_args[@]}"
if [ ${PIPESTATUS[0]} -eq 0 ];then
  echo "Installation completed successfully"
  echo "After the reboot, open http://$(hostname).local/ (or this Pi's IP address)."
  sudo reboot
else
  echo "The installation exited unsuccessfully."
  exit 1
fi
