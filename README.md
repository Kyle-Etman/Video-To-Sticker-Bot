# 🎬 Videos 2 Sticker Robot

A powerful Telegram bot that converts **Videos**, **Photos**, and **GIFs** into
Telegram Stickers instantly using FFmpeg!

> 🤖 Bot Link → [@Videos_2_sticker_robot](https://t.me/Videos_2_sticker_robot)
> 📢 Updates Channel → [@KIRA_BOTS](https://t.me/KIRA_BOTS)
> 👑 Owner → [@zoastra](https://t.me/zoastra)

---

## ✨ Features

- 🎬 **Video → Sticker** (up to 30s, longer videos auto-trimmed)
- 🖼️ **Photo → Sticker** (static WebP sticker)
- 🎞️ **GIF → Sticker** (animated WebM sticker)
- 📊 **Format Info** (resolution, duration, size shown before conversion)
- ✂️ **Auto Trim** (videos longer than 30s are trimmed automatically)
- ⚙️ **Live Progress Animation** (animated progress bar during conversion)
- 🎨 **My Stickers Counter** (/mystickers command)
- 👑 **Admin Panel** (broadcast, stats, ban/unban, maintenance mode)
- 💾 **JSON Database** (tracks all users & groups automatically)
- 🌐 **24/7 Hosting** (Render.com + UptimeRobot keep-alive)

---

## 🤖 Bot Commands

### 👤 User Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot & see welcome message |
| `/help` | Show all commands & usage guide |
| `/mystickers` | See how many stickers you've created |
| `/whoami` | Check your Telegram User ID |

### 👑 Owner Commands (Admin Only)

| Command | Description |
|---------|-------------|
| `/stats` | View bot statistics (users, stickers, groups) |
| `/ban <user_id>` | Ban a user from using the bot |
| `/unban <user_id>` | Unban a user |
| `/maintenance on\|off` | Toggle maintenance mode |
| `/broadcast` | Reply to any message to broadcast it to all users & groups |

---

## 🛠️ Tech Stack

| Tool | Purpose |
|------|---------|
| Python 3.11 | Core language |
| python-telegram-bot 22.8 | Telegram Bot API wrapper |
| FFmpeg | Video/Photo/GIF conversion engine |
| Flask | Keep-alive web server (for Render free tier) |
| JSON | Lightweight database for users & settings |
| Docker | Containerized deployment on Render.com |

---

## 🚀 Self Hosting Guide

### 📋 Requirements

- Python 3.11+
- FFmpeg installed on system
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Your Telegram User ID (from [@userinfobot](https://t.me/userinfobot))

### 📦 Installation (Termux / Linux)

```bash
# Clone the repo
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name

# Install Python dependencies
pip install -r requirements.txt

# Install FFmpeg (Termux)
pkg install ffmpeg

# Create .env file
echo "BOT_TOKEN=your_bot_token_here" > .env
echo "OWNER_ID=your_telegram_id_here" >> .env

# Run the bot
python main.py
