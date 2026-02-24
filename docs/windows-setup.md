# Windows Setup Guide

[← Back to README](../README.md)

This guide walks you through setting up Octobatch on Windows from scratch. It assumes no prior experience with Python or the command line — just follow each step in order. The whole process takes about 15 minutes.

If you're already comfortable with the command line, the [quickstart in the README](../README.md#quickstart) has everything you need in one block.

---

## 1. Open Command Prompt

Click the **Start** menu (or press the **Windows key**), type **cmd**, and press Enter. You'll see a black window with a blinking cursor — this is where you'll type commands.

**Tip:** To paste into Command Prompt, right-click anywhere in the window.

---

## 2. Install Python

Python is the programming language Octobatch is written in. You don't need to know Python to use Octobatch — you just need it installed.

**Already have Python?** Type `python --version` and press Enter. If you see `Python 3.10` or higher, skip to Step 3.

1. Go to [python.org/downloads](https://www.python.org/downloads/) and click the big yellow **Download Python** button.
2. Run the installer. On the **very first screen**, check the box that says **"Add Python to PATH"** — this is important, don't skip it.
3. Click **Install Now** and wait for it to finish (about a minute).
4. **Close your Command Prompt and open a new one** (this is necessary so the new PATH takes effect).

Verify it worked:

```cmd
python --version
```

You should see `Python 3.12.x` or similar (anything 3.10 or newer is fine).

---

## 3. Install Git

Git is a tool that downloads and manages source code. You'll use it to download Octobatch.

**Already have Git?** Type `git --version` and press Enter. If you see a version number, skip to Step 4.

1. Go to [git-scm.com/download/win](https://git-scm.com/download/win) — the download should start automatically.
2. Run the installer. The default settings are fine — just click **Next** through each screen and then **Install**.
3. **Close your Command Prompt and open a new one** so the system picks up Git.

Verify it worked:

```cmd
git --version
```

---

## 4. Download Octobatch

This downloads Octobatch into a new folder:

```cmd
git clone https://github.com/andrewstellman/octobatch.git
cd octobatch
```

The first command downloads the entire project. The second command moves into the new folder. Your Command Prompt should now show something like `C:\Users\YourName\octobatch>`.

---

## 5. Set up Python for Octobatch

These three commands create a private workspace for Octobatch and install everything it needs. Run them one at a time:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Here's what each line does:

- **Line 1** creates a "virtual environment" — a private copy of Python just for Octobatch, so it doesn't interfere with anything else on your computer. This is a standard Python practice.
- **Line 2** activates it. You'll see `(.venv)` appear at the start of your prompt — that means you're in the virtual environment.
- **Line 3** installs the libraries Octobatch depends on.

This takes about 30 seconds. You'll see text scrolling by as packages install — that's normal.

**You only need to do this once.** The launcher scripts activate the virtual environment automatically from now on.

---

## 6. Get a Gemini API key

Octobatch works by sending requests to AI services over the internet. When you run a pipeline, Octobatch sends prompts to a service like Google's Gemini, which processes them and sends back results. This is similar to how a website might connect to a payment processor or a mapping service — your software talks to their software.

To use these services, you need an **API key**. An API key is a long string of characters (like `AIzaSyD-abc123...`) that identifies your account. It's how the service knows who's making the request, tracks your usage, and bills you if you exceed the free tier. Think of it like a library card — it identifies you so the library knows who checked out the book.

Google's Gemini has a free tier that requires no credit card and is more than enough to run the demo pipelines (up to 1,000 requests per day depending on the model).

Here's how to get your key:

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with your Google account (any Gmail account works)
3. Click **Create API Key**
4. You'll see a long string of characters. Click the copy button next to it.

**Keep your API key private** — don't share it publicly or post it online. Anyone with your key could make requests on your account.

Now create a file that stores the key. Paste this into Command Prompt, replacing `your-key-here` with the key you just copied:

```cmd
echo GOOGLE_API_KEY=your-key-here > .env
```

This creates a tiny file called `.env` in the octobatch folder. You'll only need to do this once.

**Note:** On Windows, don't put quotes around the value — `echo GOOGLE_API_KEY=your-key > .env` (no quotes). Quotes would be saved literally into the file.

**Want to use other AI providers?** You can add OpenAI or Anthropic keys later by opening `.env` in Notepad and adding each key on its own line:

```
GOOGLE_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
```

---

## 7. Launch Octobatch

```cmd
octobatch-tui.bat
```

You should see the Octobatch dashboard with Otto the Octopus welcoming you:

![Octobatch TUI — Welcome screen](docs/images/welcome-to-octobatch.png)

***If you only set up a Gemini key, you'll see warnings about missing OpenAI and Anthropic keys — that's fine. Octobatch works with any single provider.***

This is a full mouse-and-keyboard application that runs inside your Command Prompt. Click on buttons, table rows, and form fields, or use the keyboard shortcuts shown in the footer bar.

---

## 8. Run your first pipeline

Press **N** to open the New Run dialog. Select **DrunkenSailor**, set the provider to **Gemini**, change Max Units to **5**, pick **Realtime** mode, and click **Start Run**.

The dashboard shows five random walk simulations flowing through the pipeline. Each unit gets a ✓ as it completes and validates. The whole thing takes a few seconds.

Press **Escape** to return to the home screen.

**You just ran your first AI pipeline.** Head back to the [README](../README.md#try-batch-mode-50-cheaper) to try batch mode and learn how to create your own pipelines.

---

## Launching Octobatch in the future

Every time you want to use Octobatch, open Command Prompt and run:

```cmd
cd octobatch
octobatch-tui.bat
```

That's it — the launcher script handles everything else automatically.

---

## Troubleshooting

**`python is not recognized`** — Python isn't in your PATH. Re-run the Python installer and make sure you check **"Add Python to PATH"** on the first screen. Then close and reopen Command Prompt.

**`git is not recognized`** — Git isn't in your PATH. Re-run the Git installer with default settings, then close and reopen Command Prompt.

**The terminal flashes and disappears** — You may have double-clicked the `.bat` file instead of running it from Command Prompt. Open Command Prompt first, `cd` into the octobatch folder, then run `octobatch-tui.bat`.

**`pip install` fails with a permission error** — Make sure you activated the virtual environment first (you should see `(.venv)` in your prompt). If not, run `.venv\Scripts\activate` and try again.

**The TUI looks garbled or colors are wrong** — Windows Command Prompt has limited terminal support. Try using **Windows Terminal** instead (search for it in the Start menu, or install it from the Microsoft Store). It handles colors and Unicode much better.
