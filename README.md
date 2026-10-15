# Video Desk

Video Desk is a browser app that runs on your computer. You write a prompt, and it asks the official xAI video API to make a short MP4 with your own API key. The page is not uploaded to a host. Finished clips are saved in the `videos` folder next to this app.

xAI charges the credits on that API key for each second of video. The app itself has no subscription and does not resell access.

## Run it

Install Python 3.9 or newer, then from this folder:

```bash
python3 server.py
```

On Windows, `py -3 server.py` does the same thing. `run.sh` and `run.bat` are shortcuts.

Leave the terminal window open, and use the page at http://127.0.0.1:8787

## API key

1. Create an account at https://console.x.ai
2. Add credits there. Video generation draws from those credits.
3. Create an API key and paste it into Video Desk.

The key stays in this browser if you ask it to. The local server forwards it only to `https://api.x.ai` and does not write it to disk.

Model and price details: https://docs.x.ai/developers/model-capabilities/video/generation

## What you can make

- Text to video, 1 to 15 seconds
- An optional PNG, JPEG, or WebP starting image
- Imagine Video 1.5 Lite or Imagine Video 1.5
- 480p, 720p, or 1080p

Generation usually takes a few minutes. The MP4 is saved under `videos/`. The page lists each clip by the prompt you wrote. Play stays on the page, and Download saves that MP4 to your computer.

## Checks

```bash
python3 -m unittest tests.test_server -v
```
