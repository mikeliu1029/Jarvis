# J.A.R.V.I.S.

A local, voice-driven assistant for Windows with a British voice, a dry wit, and hands on your PC.
Say **"Hey Jarvis"** and talk to it like you would the real one.

**Voice engine.** He talks through **Gemini Live**: it hears you and answers in a natural voice, with the first word in about half a second, and you can talk over him on headphones. It needs a free key, which the Settings window walks you through getting.

**What stays on your PC.** All tools run locally: apps, games, the screen, memory, the HUD and the safety checks. Jarvis also decides locally whether you were talking to him. If you didn't say "Jarvis" and you're not mid-conversation, his reply is never played and no tool runs.

**Privacy.** While the mic is on, room audio streams to Google; F4 mutes it. Gemini's free tier lets Google use what it's sent to improve its services.

**Fallback.** If Gemini is unreachable or out of free use, he can switch to a fully offline voice and brain (Settings → Offline mode).

## Install (Windows 10/11)

1. **Download.** On this page, click the green **Code** button → **Download ZIP**. Right-click the ZIP → **Extract All** → pick a folder such as Documents, then open the extracted folder.
2. **Double-click `run_jarvis.bat`.** If Windows says "Windows protected your PC", click **More info → Run anyway**; Windows shows this for any script downloaded from the internet. The first run installs everything, including Python if you don't have it. It takes about five minutes, then puts a **Jarvis button on your desktop** and opens Jarvis.
3. **Paste your free key.** Jarvis's Settings window opens on *Getting started*. Click **Get my free key**, follow the four steps on screen, then click **Paste** and **Save & test**. Jarvis restarts and greets you.
4. **Say "Jarvis, what's the news?"**

From then on, start him with the desktop button.

**Everything else is in the Settings window:** the ⚙ SETTINGS button in Jarvis's window, or just say "Jarvis, open settings". It covers:
- your name and city
- his voice, your microphone and speakers
- Google (Calendar, Gmail, Classroom)
- the morning briefing and the pop-up
- safety, his personality, offline mode, and starting with Windows

No files to edit.

**Updating.** Download the new ZIP and extract it over the old folder (choose *Replace*). Your key, settings and memory aren't in the ZIP, so they're kept. New components install by themselves on the next start.

## Using Jarvis

Start him with the desktop button (or `run_jarvis.bat`). His window has:
- **Reactor core and waveform:** they react to Jarvis's voice and yours.
- **System monitor:** CPU, memory, network, GPU and GPU temperature.
- **Typewriter activity log** and a **command box**.
- **Results panel:** search, news and briefing results.
- **Buttons:** Talk, Interrupt (Esc), Mute mic (F4), Memory and Settings.

Close the window to shut Jarvis down, or say "Jarvis, shut down".

**Pop-up.** While he talks, a small bar slides down from the top of your screen. It shows a mini reactor, his voice level and his words. Each caption appears as its audio is heard and types out at speaking pace, and the bar slides back up when he finishes. It never takes focus and clicks pass through it, so it's safe over games. If the words run ahead of or behind his voice, adjust the timing slider in Settings → Pop-up & display.

For tinkerers, `run_jarvis.bat` takes options: `--text` (keyboard only), `--mute`, `--no-hud`, `--selftest`, `--settings`, `--say "text"`, `--ask "question"`.

## Built on MARK LVI

The design follows **MARK LVI** by FatihMakes (CC BY-NC 4.0; see `NOTICE.md`), adapted to run fully locally:

- **It says what it checked.** Every action reports `done`, `unverified`, `failed`, `ask` or `started`. Opening an app watches for its window to appear. Volume and file writes are read back. Jarvis never claims an unconfirmed action as done.
- **Raise, don't duplicate.** "Open Spotify" while it's running brings it to the front instead of starting a second copy.
- **Close by name, never itself.** `close_app` asks the app politely to close, so it can prompt you to save. It refuses to close Jarvis or the program running it, and asks first before closing the window you're working in. Close shortcuts like Alt+F4 get the same treatment.
- **Untrusted-content boundary.** Text from web pages, files, emails, the clipboard and the screen reaches the model marked as data. An instruction hidden inside it is reported, not obeyed.
- **Undo.** "Undo that" reverses Jarvis's own last change: a file it wrote or created, the volume, or dark mode.
- **Memory that actually remembers.** Facts are stored by category (identity, preferences, projects, relationships, wishes, notes) with nothing silently dropped. The prompt carries a core plus an index of everything else, which Jarvis looks up on demand. The 🧠 MEMORY panel shows every fact and lets you delete any of them.
- **Session summaries.** After a few minutes of quiet, and again at shutdown, Jarvis writes a sentence about what you were doing and what's left. The next start-up picks up from it, once. Games you launch are remembered as interests.
- **Watched topics.** "Keep an eye on Nvidia news" adds a topic. Jarvis mentions genuinely new headlines when you're not mid-conversation.
- **Self-knowledge.** The prompt lists his tools and limits, generated from what's actually installed.
- **Drop-in plugins.** Copy `plugins/_template.py` to `plugins/anything.py` and its tools appear on the next launch. A broken plugin is reported and skipped.
- **No loops.** If the model asks for the same action twice in one turn, the second call is answered from the first result, so an app never opens four times.

## What to say

| You say | Jarvis does |
|---|---|
| "I'm going to stream Valorant" | Opens Valorant and OBS, tells you the latest patch, and opens an article about the big new game in that genre (e.g. the new Call of Duty) |
| "Show me the trailer" | Plays that game's trailer on YouTube in Chrome |
| "Open Spotify" / "Launch Steam" | Finds the app in the Start menu and opens it |
| "Search Windows for Bluetooth settings" | Uses the taskbar search box, types, and opens the top result |
| "Play lofi beats on YouTube" | Finds and plays the top video |
| "Play Back in Black on Spotify" | Opens Spotify search and tries to press play on the top result |
| "Open a new tab and go to bbc.co.uk" | Shortcuts plus typing inside apps |
| "Click Sign in" / "Click the Download button" | Finds it via accessibility, on-screen text, or vision, then clicks |
| "What's on my screen?" / "Read this page to me" | Looks at the screen (vision model) or reads it (OCR) |
| "Type: see you at eight" | Types into the focused window |
| "Daily briefing" / "Catch me up" | Weather, calendar, headlines, tasks, email, and PC status |
| "News about Nvidia" / "Tech headlines" | RSS and Google News |
| "Who won the game last night?" | Searches the web and answers |
| "What's on my calendar tomorrow?" / "Add dentist Thursday at 3" | Google Calendar |
| "Any important emails?" / "Reply to Sarah saying I'll be there" | Gmail (sending always asks first) |
| "What homework is due this week?" / "Is anything missing?" | Your son's Google Classroom |
| "Open my history essay" | Finds it in Google Drive and opens it in Chrome |
| "Jarvis, shut down" | Closes Jarvis. "Shut down the PC" turns the computer off (after asking) |
| "Remind me in 20 minutes to stretch" | Spoken reminder at the time |
| "Add buy milk to my list" | To-do list |
| "Remember I take my coffee black" | Long-term memory, used in every later conversation |
| "Close Spotify" | Closes it politely, then checks that it's gone |
| "Undo that" | Reverses Jarvis's own last change |
| "Keep an eye on SpaceX news" | Watches the topic and mentions new headlines |
| "Turn on dark mode" | Windows theme, read back, undoable |
| "How's the system doing?" | CPU, GPU, RAM, disk, and the busiest processes |
| "Set volume to 30" / "Next track" / "Lock the PC" | Media keys, volume, and power |
| "Find my resume" / "Open Downloads" | File search and folders |

How listening works:
- **The mic stays on until you mute it.** Use F4 or the HUD button.
- **Name him anywhere in the sentence:** "Jarvis, boot up Cyberpunk" or "what's the weather, Jarvis?". Speech without his name is ignored.
- **Follow-ups:** for 20 seconds after he answers you can keep talking without his name. Saying just "Jarvis?" gets a "Yes, sir?".
- **Interrupt him** with "Hey Jarvis", Ctrl+Alt+J, Esc, or "stop".
- **Check the devices:** the activity log shows which microphone and speaker he's using at start-up. Change them in Settings → Voice & microphone.

On start-up:
- **Briefing:** the first launch of the day gets the full briefing, and every later launch gets a quick spoken news rundown.
- **Article:** he then opens one article in Chrome that he thinks you'd like, based on what he knows about you (`[briefing] open_article_on_launch`).
- **Can't change anything:** during this automatic part he can only read things and open that one article. He can't launch apps or change settings until you ask for something.
- **Games:** opening a game gets you its latest update and a line of context while it loads.

Links, articles and trailers open in Chrome even though Edge is the default browser. Set `[apps] browser = "default"` to use the default browser instead.

## Folder map

```
Jarvis/
├── run_jarvis.bat · setup.ps1 · connect_google.bat · requirements.txt · jarvis.ico
├── .env                   your keys (written by the Settings window; never uploaded)
├── mcp_server.py          MCP hooks, outbound: Jarvis's tools for Claude Desktop / Claude Code
├── config/
│   ├── settings.toml      the defaults for every option
│   ├── my_settings.json   your changes from the Settings window (never uploaded)
│   ├── persona.md         the system prompt (personality and rules); edit to taste
│   └── mcp_servers.json   MCP hooks, inbound: calendar/email/other servers Jarvis connects to
├── plugins/               drop-in skills (copy _template.py)
├── NOTICE.md              attribution for the MARK LVI-derived parts
├── memory/                plain JSON you can read or edit
│   ├── long_term.json     what Jarvis knows about you, by category
│   ├── tasks.json · reminders.json · monitors.json · state.json
│   └── logs/              daily conversation log (YYYY-MM-DD.jsonl) + jarvis.log
└── jarvis/
    ├── assistant.py       orchestrator: input routing, confirmations, reminders, proactive alerts
    ├── brain/             ollama.py (local, default) · claude.py (optional) · base.py
    ├── voice/             listener (wake word → record → transcribe) · tts · stt · audio
    ├── tools/             apps · keyboard_mouse · screen · ocr · web · news · weather
    │                      briefing · system · files · productivity · mcp_bridge · outcome
    ├── live.py            Gemini Live voice engine (streaming audio, tools, "were you talking to me?" gate)
    ├── hud/               desktop.py (PyQt6 window) · popup.py (top-of-screen bar) · server.py + index.html
    └── memory.py · undo.py · plugin_loader.py · persona.py · vision.py · winutil.py · config.py · selftest.py
```

The Python environment and models (about 2 GB) live in `%USERPROFILE%\.jarvis`, so OneDrive doesn't sync them.

## Voice pipeline (modular)

Set `[tts] engine` in `config/settings.toml`:

| engine | where | voice | needs |
|---|---|---|---|
| `kokoro` (default) | local | `bm_george`, `bm_fable`, `bm_lewis`, `bm_daniel`, or blends like `"bm_george:0.6,bm_fable:0.4"` | nothing |
| `edge` | online, free | `en-GB-RyanNeural`, `en-GB-ThomasNeural` | internet |
| `elevenlabs` | cloud | "Daniel" by default, or any voice ID | `ELEVENLABS_API_KEY` in `.env` |
| `fishaudio` | cloud | any fish.audio voice model, including community JARVIS voices | `FISH_AUDIO_API_KEY` + `FISH_AUDIO_VOICE_ID` |
| `sapi` | local | Windows voices (no British voice installed on this PC) | nothing |

If an engine fails, Jarvis falls back to Kokoro, then Edge, then SAPI. Test a voice with `run_jarvis.bat --say "Good evening, sir."`.

Speech recognition is `[voice] stt_model` (default `small.en` on CPU). The wake word is openWakeWord's "hey jarvis". If it wakes by accident, raise `wake_threshold`.

## Brain

- **Local (default):** Ollama with `qwen3-vl:8b`, a model that can call tools and see the screen. For snappier replies, `ollama pull qwen3-vl:8b-instruct` and set `model` to that (it skips the thinking step).
- **Claude (optional):** set `[brain] provider = "anthropic"` and add the Claude key in Settings → Getting started → Other services. The default model is `claude-opus-5-5` at `effort = "low"` for quick voice replies. `claude-sonnet-5-5` and `claude-haiku-5-5` are faster and cheaper. Server-side refusal fallbacks are switched on (`fallbacks: "default"`), so a false-positive safety decline is retried on another Claude model instead of failing.

## Google (Calendar, Gmail, Contacts, Drive, Classroom)

Open **Settings → Google** (or double-click `connect_google.bat`). The first time, it walks you through making a free, private Google Cloud project for Jarvis (about five minutes, each step has an *Open page* button), picks up the key file from your Downloads by itself, and signs you in. Google requires that project: Jarvis isn't a published app, so it needs a key of its own. In Google's *Audience* page, add your account (and your son's) under **Test users**; Google then asks you to sign in again every 7 days, and Jarvis opens that sign-in for you when it's due.

| Service | What Jarvis can do |
|---|---|
| Calendar | Read all your calendars; add, move and delete events (deleting asks first; "undo that" reverses an add or move) |
| Gmail | Unread mail in Primary, search, read and summarise, drafts, send and reply (both ask first), archive |
| Contacts | Turns names into addresses for "email Mum…", and looks up numbers |
| Drive | Find files, open them in Chrome, read Google Docs/Sheets/Slides |
| Classroom | Your son's missing work, what's due soon, recent grades and teacher announcements (read-only) |

Notes:
- **Your son's Classroom.** The setup offers to sign in your son's school account as well. School accounts often block apps their IT admin hasn't approved, especially for students under 18. The block page usually has a "Request access" button. Until the school approves it, Jarvis reads the **Classroom guardian summary** emails in your Gmail instead; ask the teacher to add you as a guardian if you don't get them.
- **"Google hasn't verified this app."** You'll see this at sign-in. It's expected, because the app is your own: click Advanced, then Go to Jarvis.
- **Where the sign-ins live.** They're saved in `%USERPROFILE%\.jarvis\google`, outside OneDrive. To disconnect, delete them there or remove Jarvis at myaccount.google.com/permissions.
- **Briefing.** The daily briefing adds your calendar, your Primary unread email and your son's schoolwork.

**Without Google:** put an iCal link in `CALENDAR_ICS_URL`, and an IMAP app password in `IMAP_USER` / `IMAP_PASSWORD`. Calendar and unread-mail checks then work read-only.

## MCP hooks

**Inbound (anything else).** Jarvis connects at startup to each enabled server in `config/mcp_servers.json`, and their tools become Jarvis's tools. Google is built in now, so you don't need the Google examples below. Examples are included but disabled:
- `@cocal/google-calendar-mcp`
- `@gongrzhe/server-gmail-autoauth-mcp`
- `@modelcontextprotocol/server-filesystem`

Each one needs Node.js (`winget install OpenJS.NodeJS.LTS`). Remote servers work with `"url": "https://…/mcp"`. If you add a lot of tools, raise `num_ctx`.

**Outbound (use Jarvis's hands from Claude).**
```
claude mcp add jarvis -- "%USERPROFILE%\.jarvis\venv\Scripts\python.exe" "<path to this folder>\mcp_server.py"
```
This gives Claude Code or Claude Desktop 27 tools: open apps, click on screen, read screen, news, weather, and more. Commands, power actions, process kills, and file overwrites are withheld unless you set `[mcp_server] allow_confirmed_tools = true`.

## Safety

- **Confirmation:** PowerShell commands that change things, shutdown/restart/sleep, killing processes, and overwriting files all ask first ("Shall I proceed, sir?"). Read-only commands such as `Get-*`, `ipconfig`, and `git status` run without asking. See `[safety]`.
- **Emergency stop:** move the mouse into a screen corner to abort any automation (pyautogui fail-safe).
- **HUD:** listens on 127.0.0.1 only, and every command needs a per-run token.
- **Admin windows:** Jarvis can't type into windows running as administrator unless Jarvis itself is.

## Troubleshooting

- `run_jarvis.bat --selftest` checks every subsystem.
- Logs are in `memory/logs/jarvis.log`.
- **"Ollama isn't running":** start Ollama from the Start menu.
- **Wrong microphone:** set `[voice] input_device = "Arctis"` (any part of the device name).
- **First reply is slow:** the model is loading into GPU memory. It stays loaded for `keep_alive` (30 minutes).
- **Start with Windows:** `powershell -ExecutionPolicy Bypass -File setup.ps1 -Autostart`.
