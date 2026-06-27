# Daily AI startup post timer design

## Context

The project already has a Django management command named `create_startup_post`.
It creates one DeepSeek-generated daily article and uses a `daily:YYYY-MM-DD` tag
to avoid creating duplicate daily posts for the same author on the same day.

The production server already has DeepSeek, Pexels, database, Gunicorn, Nginx,
and Cloudflare Tunnel configuration in place. The user will run all server
commands manually; Codex must not initiate SSH connections for this work.

## Requirements

- Run the daily AI article job every day at server time `08:30`.
- Publish directly instead of creating a draft.
- Use the Django username `白车轴草` as the fixed post author.
- Allow the command to attach a Pexels cover image when available.
- Do not retry automatically on failure, to avoid repeated API consumption.
- Keep API keys and credentials out of systemd unit files and Git.
- Preserve the command's existing same-day duplicate protection.

## Design

Create two production systemd units outside the Git repository:

- `/etc/systemd/system/clover-blog-startup-post.service`
- `/etc/systemd/system/clover-blog-startup-post.timer`

The service runs from `/opt/clover-blog`, uses the existing virtual environment
Python executable, and calls:

```bash
/opt/clover-blog/.venv/bin/python /opt/clover-blog/白车轴草/manage.py create_startup_post --username 白车轴草
```

The timer uses `OnCalendar=*-*-* 08:30:00` and `Persistent=true`. If the server
is off at `08:30`, systemd runs the missed job after boot. The service uses
`Type=oneshot` and has no automatic restart policy.

## Verification

After installing the units, verify with:

- `sudo systemctl daemon-reload`
- `sudo systemctl enable --now clover-blog-startup-post.timer`
- `sudo systemctl list-timers --all | grep clover-blog-startup-post`
- `sudo systemctl start clover-blog-startup-post.service`
- `sudo systemctl status clover-blog-startup-post.service --no-pager -l`
- `sudo journalctl -u clover-blog-startup-post.service --no-pager -n 80`

The manual service start should create today's article if none exists for the
configured author and daily tag. If today's daily article already exists, it
should log that the daily article already exists and exit successfully.
