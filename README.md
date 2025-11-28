# Telegram to WebDAV Bot

This is a Telegram bot that captures your messages—including text, photos, videos, documents, and voice notes—and saves them as organized Markdown files to a WebDAV server. It's designed to be a personal digital filing cabinet, seamlessly integrating your Telegram interactions with your private cloud storage.

Audio and voice messages are automatically transcribed using the OpenAI API, with the resulting text included in the note for easy searching and reference.

## Features

-   **Comprehensive Capture**: Saves text, photos, videos, documents, and voice messages.
-   **Automatic Audio Transcription**: Utilizes OpenAI's `gpt-4o-mini-transcribe` model to convert audio/voice messages to text.
-   **Markdown Formatting**: Creates well-structured Markdown files. Media is saved in a `data` subfolder and linked within the note.
-   **Organized Storage**: Notes are automatically organized into daily folders on your WebDAV server (e.g., `/notes/2023-10-27/`).
-   **Secure Access**: New users must provide a password to gain access. Authorized user IDs are stored locally.
-   **Easy Deployment**: Runs as a single Python script with minimal dependencies.

## How It Works

1.  You send a message (text, media, or voice) to the bot.
2.  The bot first checks if your user ID is authorized. If not, it prompts for a password via the `/start` command.
3.  For authorized users, the bot processes the message:
    -   Any attached media (photos, documents, etc.) is downloaded.
    -   If the message is an audio or voice note, it is sent to the OpenAI API for transcription.
    -   A Markdown file is generated containing the message text, the audio transcription (if any), and links to the downloaded media.
4.  The Markdown file and all associated media files are uploaded to your WebDAV server, organized into a folder for the current date.
5.  The bot replies to your message with the remote path of the newly created note.

### Example WebDAV File Structure

Your notes will be organized on the WebDAV server as follows:

```
/notes/                  <-- Your WEBDAV_ROOT
└── 2024-05-21/
    ├── note_153045.md
    └── data/
        ├── fileid_of_photo.jpg
        ├── fileid_of_voice.ogg
        └── fileid_of_document.pdf
```

The `note_153045.md` file might look like this:

```markdown
![](/data/fileid_of_photo.jpg)

---

**Распознанное аудио:**

This is the transcribed text from the voice message.

---

Here is the original caption for the photo.
```

## Setup and Installation

### Prerequisites

-   Python 3.8+
-   A Telegram Bot Token (get one from [@BotFather](https://t.me/BotFather))
-   WebDAV server credentials (URL, username, password)
-   An OpenAI API Key (optional, for audio transcription)

### Instructions

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/vmkadrov/telegram2webdav.git
    cd telegram2webdav
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure environment variables:**
    Create a `.env` file by copying the example:
    ```bash
    cp .env.example .env
    ```
    Now, edit the `.env` file with your credentials:

    -   `TELEGRAM_BOT_TOKEN`: Your Telegram bot token.
    -   `WEBDAV_URL`: The full URL to your WebDAV endpoint.
    -   `WEBDAV_USERNAME`: Your WebDAV username.
    -   `WEBDAV_PASSWORD`: Your WebDAV password.
    -   `WEBDAV_ROOT`: The root directory on the server to store notes (e.g., `/notes`). Defaults to `/notes`.
    -   `NOTES_PASSWORD`: A secret password users will need to enter to get access to the bot.
    -   `OPENAI_API_KEY`: Your OpenAI API key. If left blank, audio transcription will be disabled.

4.  **Run the bot:**
    ```bash
    python app.py
    ```

## Usage

1.  Start a chat with your bot in Telegram.
2.  Send the `/start` command.
3.  The bot will ask for a password. Enter the `NOTES_PASSWORD` you configured in the `.env` file.
4.  Once authorized, you can start sending messages. Any text, photo, video, document, or voice note you send will be automatically saved to your WebDAV server.
5.  The bot will confirm each save with a reply containing the path to the note file.