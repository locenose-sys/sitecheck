# sitecheck - which sites accept this server?

One-off test page. Deploy as a Render web service (free is fine for a quick test):

- Build command: `pip install -r requirements.txt`
- Start command: `python sitecheck_web.py`
- Environment variable: `PASSWORD` = a password you choose (never share it)

Open the service's URL, enter the password, paste links, press Run. Results show
PASS/FAIL per site, the server's outbound IP, and whether ffmpeg exists.
Delete the service afterwards. Nothing is sent to Telegram; files are removed after each run.
