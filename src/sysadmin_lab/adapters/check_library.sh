# Linux Admin Lab check library.
#
# The host prepends this file to every script check before sending it to a guest, so a
# learner with root on the guest cannot edit it. A function that detects an unmet
# requirement prints one learner-facing sentence on stderr and exits non-zero; the checker
# shows the last stderr line as the reason a check failed.

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

# expect_eq ACTUAL EXPECTED WHAT
expect_eq() {
    [[ "$1" == "$2" ]] || fail "$3 is ${1:-empty}; expected $2"
}

# retry ATTEMPTS COMMAND...: run COMMAND once per second until it succeeds.
retry() {
    local attempts=$1 attempt
    shift
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        "$@" && return 0
        ((attempt < attempts)) && sleep 1
    done
    return 1
}

# unit_value UNIT PROPERTY: print one systemd unit property.
unit_value() {
    systemctl show --property="$2" --value -- "$1"
}

# fstab_field MOUNTPOINT COLUMN: print SOURCE, FSTYPE, or OPTIONS as written in /etc/fstab.
fstab_field() {
    findmnt --fstab --noheadings --output "$2" --mountpoint "$1" 2>/dev/null | tail -n 1
}

# has_option OPTIONS NAME: true when a comma-separated option list contains NAME or NAME=VALUE.
has_option() {
    local options=",$1,"
    [[ "$options" == *",$2,"* || "$options" == *",$2="* ]]
}

# option_value OPTIONS NAME: print VALUE from the last NAME=VALUE in a comma-separated list.
option_value() {
    local IFS=, item value=
    for item in $1; do
        [[ "$item" == "$2="* ]] && value=${item#*=}
    done
    printf '%s' "$value"
}

# sysctl_assignment KEY: read sysctl.d syntax on stdin and print the last value set for KEY.
sysctl_assignment() {
    awk -v key="$1" '
        {
            line = $0
            sub(/^[ \t]+/, "", line)
            if (line == "" || line ~ /^[#;]/ || index(line, "=") == 0) next
            sub(/^-/, "", line)
            name = substr(line, 1, index(line, "=") - 1)
            value = substr(line, index(line, "=") + 1)
            gsub(/[ \t]/, "", name)
            gsub("/", ".", name)
            gsub(/^[ \t]+|[ \t]+$/, "", value)
            if (name == key) found = value
        }
        END { if (found != "") print found }'
}

# sysctl_configured KEY: print the value that systemd-sysctl applies for KEY at boot.
sysctl_configured() {
    systemd-analyze cat-config sysctl.d 2>/dev/null | sysctl_assignment "${1//\//.}"
}
