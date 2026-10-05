#!/bin/bash
# Start the mailing processes as root, outside tmux, when `sudo -E python3 ...` inside a tmux window cannot ask for a
# password: the desk first (the batches refuse to start without its heartbeat), then the three batches that are still
# open. Run it from a terminal that can type the sudo password:  bash tools/mailing_up.sh
# A process that cannot run (its send time passed) says so in its .out file; nothing is sent by this script itself.
set -u
cd "$(dirname "$0")/.."
W=data/email-analysis/cases/nurse-79/mailer
sudo -v || exit 1
start() {
  out=$1; shift
  sudo -E nohup python3 -u "$@" >> "$out" 2>&1 &
  echo "started: $*  (output: $out)"
}
if pgrep -f "daria_desk.py run" >/dev/null; then
  echo "a desk already runs, not starting a second one"
else
  start data/email-analysis/desk/desk.out tools/daria_desk.py run data/email-analysis/desk/daria.json
fi
echo "waiting for the desk heartbeat (up to 10 min)"
for _ in $(seq 1 60); do
  sleep 10
  [ -n "$(find data/email-analysis/desk/heartbeat.json -mmin -2 2>/dev/null)" ] && break
done
[ -n "$(find data/email-analysis/desk/heartbeat.json -mmin -2 2>/dev/null)" ] || { echo "the desk has no fresh heartbeat; see data/email-analysis/desk/desk.out"; exit 1; }
for spec in "campaign.w2.json nurse79-2-20261005-1121 w2/send-nurse79-2-20261005-1121.out" \
            "campaign.w2.json nurse79-2-20261005-1408 w2/send-nurse79-2-20261005-1408.out" \
            "campaign.json nurse79-20261005-1408 send-nurse79-20261005-1408.out"; do
  read -r cfg batch out <<<"$spec"
  if pgrep -f "clinic_mailer.py send $W/$cfg $batch" >/dev/null; then echo "$batch already runs"; continue; fi
  start "$W/$out" tools/clinic_mailer.py send "$W/$cfg" "$batch" --live
done
echo "done; ps -eo pid,args | grep -E 'daria_desk|clinic_mailer' shows them"
