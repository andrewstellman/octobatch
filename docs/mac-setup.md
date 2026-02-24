# Mac Setup Guide

[← Back to README](../README.md)

This guide walks you through setting up Octobatch on a Mac from scratch. It assumes no prior experience with Python or the Terminal — just follow each step in order. The whole process takes about 15 minutes.

If you're already comfortable with the command line, the [quickstart in the README](../README.md#quickstart) has everything you need in one block.

---

## 1. Open Terminal

Terminal is the Mac application that lets you type commands. Press **⌘ Space** to open Spotlight, type **Terminal**, and press Enter.

You'll see a window with a blinking cursor. This is where you'll type (or paste) the commands in this guide. Commands are shown in gray boxes — you can copy and paste them directly.

**Tip:** To paste into Terminal, press **⌘V** (just like in any other Mac app).

---

## 2. Install Homebrew

Homebrew is a tool that installs software on your Mac from the command line. It's the standard way to get developer tools like Python, and you only need to install it once.

**Already have Homebrew?** Type `brew --version` and press Enter. If you see a version number, skip to Step 3.

Paste this into Terminal and press Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Here's what will happen:

1. Homebrew describes what it's about to install and asks you to **press Enter** to continue.
2. It asks for your **Mac password** — the one you use to log in to your Mac. When you type it, **nothing will appear on screen** (no dots, no asterisks). That's a security feature. Type your password and press Enter.
3. It downloads and installs several tools from Apple. This takes **2–5 minutes** depending on your internet speed. You'll see a lot of text scrolling by — that's normal.
4. When it finishes, look for instructions near the bottom that say something like "Run these commands in your terminal." **Copy and paste those lines into Terminal** — they add Homebrew to your system so you can use it from now on.

Verify it worked:

```bash
brew --version
```

You should see something like `Homebrew 4.x.x`.

---

## 3. Install Python

Python is the programming language Octobatch is written in. You don't need to know Python to use Octobatch — you just need it installed on your Mac.

**Already have Python?** Type `python3 --version` and press Enter. If you see `Python 3.10` or higher, skip to Step 4.

```bash
brew install python
```

This takes about a minute. When it's done, verify:

```bash
python3 --version
```

You should see `Python 3.12.x` or similar (anything 3.10 or newer is fine).

---

## 4. Install Git

Git is a tool that downloads and manages source code. You'll use it to download Octobatch.

**Already have Git?** Type `git --version` and press Enter. If you see a version number, skip to Step 5.

Most Macs come with Git already installed. If the command above doesn't work:

```bash
xcode-select --install
```

This opens a dialog asking to install Apple's command-line developer tools (which include Git). Click **Install** and wait — it takes **2–5 minutes**.

---

## 5. Download Octobatch

This downloads Octobatch into a new folder in your home directory:

```bash
git clone https://github.com/andrewstellman/octobatch.git
cd octobatch
```

The first command downloads the entire project. The second command moves into the new folder. Your Terminal prompt should now show `octobatch` somewhere in it.

---

## 6. Set up Python for Octobatch

These three commands create a private workspace for Octobatch and install everything it needs. Copy and paste all three lines at once:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Here's what each line does:

- **Line 1** creates a "virtual environment" — a private copy of Python just for Octobatch, so it doesn't interfere with anything else on your Mac. This is a standard Python practice.
- **Line 2** activates it. You'll see `(.venv)` appear at the start of your Terminal prompt — that means you're in the virtual environment.
- **Line 3** installs the libraries Octobatch depends on (things like the Textual UI framework, YAML parser, etc.).

This takes about 30 seconds. You'll see text scrolling by as packages install — that's normal.

**You only need to do this once.** The launcher scripts activate the virtual environment automatically from now on.

---

## 7. Get a Gemini API key

Octobatch works by sending requests to AI services over the internet. When you run a pipeline, Octobatch sends prompts to a service like Google's Gemini, which processes them and sends back results. This is similar to how a website might connect to a payment processor or a mapping service — your software talks to their software.

To use these services, you need an **API key**. An API key is a long string of characters (like `AIzaSyD-abc123...`) that identifies your account. It's how the service knows who's making the request, tracks your usage, and bills you if you exceed the free tier. Think of it like a library card — it identifies you so the library knows who checked out the book.

Google's Gemini has a free tier that requires no credit card and is more than enough to run the demo pipelines (up to 1,000 requests per day depending on the model).

Here's how to get your key:

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with your Google account (any Gmail account works)
3. Click **Create API Key**
4. You'll see a long string of characters. Click the copy button next to it.

**Keep your API key private** — don't share it publicly or post it online. Anyone with your key could make requests on your account.

Now create a file that stores the key. Paste this into Terminal, replacing `your-key-here` with the key you just copied:

```bash
echo "GOOGLE_API_KEY=your-key-here" > .env
```

This creates a tiny file called `.env` in the octobatch folder. You'll only need to do this once.

**Want to use other AI providers?** You can add OpenAI or Anthropic keys later by opening `.env` in TextEdit (or any text editor) and adding each key on its own line:

```
GOOGLE_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
```

---

## 8. Launch Octobatch

```bash
./octobatch-tui
```

You should see the Octobatch dashboard with Otto the Octopus welcoming you:

![Octobatch TUI — Welcome screen](docs/images/welcome-to-octobatch.png)

***If you only set up a Gemini key, you'll see warnings about missing OpenAI and Anthropic keys — that's fine. Octobatch works with any single provider.***

This is a full mouse-and-keyboard application that runs inside your Terminal. Click on buttons, table rows, and form fields, or use the keyboard shortcuts shown in the footer bar.

---

## 9. Run your first pipeline

Press **N** to open the New Run dialog. Select **DrunkenSailor**, set the provider to **Gemini**, change Max Units to **5**, pick **Realtime** mode, and click **Start Run**.

The dashboard shows five random walk simulations flowing through the pipeline. Each unit gets a ✓ as it completes and validates. The whole thing takes a few seconds.

Press **Escape** to return to the home screen.

**You just ran your first AI pipeline.** Head back to the [README](../README.md#try-batch-mode-50-cheaper) to try batch mode and learn how to create your own pipelines.

---

## Launching Octobatch in the future

Every time you want to use Octobatch, open Terminal and run:

```bash
cd octobatch
./octobatch-tui
```

That's it — the launcher script handles everything else automatically.

---

## Troubleshooting

**`brew: command not found`** — Homebrew isn't in your PATH. Re-run the Homebrew installer, and this time carefully follow the instructions it prints at the end about adding Homebrew to your PATH.

**`python3: command not found`** — Python isn't installed or isn't in your PATH. Try `brew install python` and then open a new Terminal window.

**`git: command not found`** — Run `xcode-select --install` to install Apple's developer tools.

**`./octobatch-tui: Permission denied`** — The script needs to be executable. Run `chmod +x octobatch-tui octobatch` and try again.

**Password prompt shows nothing when I type** — That's intentional. The Terminal hides your password for security. Just type it and press Enter.
