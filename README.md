# ❤️ Relationship Memory Bot

A personal Telegram bot designed for couples to track important dates, share notes (text & photos), and calculate how long they have been together.

## ✨ Features

  * **📅 Recurring Reminders:** Tracks birthdays and anniversaries.
  * **🔔 Smart Alerts:** Sends notifications 1 month, 1 week, 1 day, and the Day Of the event.
  * **📝 Shared Notes:** Save text notes or photos (e.g., wifi passwords, gift ideas, memories).
  * **❤️ "Our Journey":** Calculates exactly how many years, months, and days you have been together.
  * **👥 Group Support:** Works perfectly in a shared group chat between you and your partner.
  * **☁️ Persistent Storage:** Uses a local SQLite database (`dates.db`).

-----

## 🛠️ Prerequisites

Before running the bot, you need:

1.  **A Telegram Bot Token:** Talk to [@BotFather](https://t.me/BotFather) on Telegram to create a new bot and get the API Token.
2.  **Python 3.11+** (For running locally).
3.  **Docker** (For running on a NAS or Server).

-----

## ⚙️ Configuration

1.  Create a file named `.env` in the same folder as your code.
2.  Add your token inside it:
    ```env
    TELEGRAM_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
    ```

-----

## 🚀 How to Run

### Method 1: Running Locally (VS Code / Terminal)

*Best for testing or running on your personal computer.*

1.  **Install Dependencies:**
    Open your terminal in the project folder and run:

    ```bash
    pip install python-telegram-bot python-dotenv APScheduler
    ```

    *(Or use `pip install -r requirements.txt` if you have one).*

2.  **Start the Bot:**
    Run the script:

    ```bash
    python main.py
    ```

3.  **Success:**
    You should see "Bot is running..." in the terminal. Go to Telegram and press `/start`.

-----

### Method 2: Docker Container (NAS / Portainer)

*Best for running 24/7 on a NAS, Raspberry Pi, or Server.*

#### 1\. Prepare the Image

Create a file named `Dockerfile` in your project folder:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY .env .
CMD ["python", "main.py"]
```

#### 2\. Build and Export

Run these commands on your computer to create a file you can upload to your NAS:

```bash
# Build the image
docker build -t relationship-bot:v1 .

# Save it to a .tar file
docker save -o relationship-bot.tar relationship-bot:v1
```

#### 3\. Deploy on Portainer (NAS)

1.  **Import:** Go to **Images** -\> **Import** -\> Upload `relationship-bot.tar`.
2.  **Create Container:** Go to **Containers** -\> **Add Container**.
3.  **Image:** Type `relationship-bot:v1`.
4.  **Restart Policy:** Set to `Always`.

#### ⚠️ 4. The Critical Step (Database Persistence)

To ensure your dates are saved even if the NAS restarts, you must map the database file.

1.  **On your NAS File Manager:** Create an **empty file** named `dates.db` in your docker folder (e.g., `/docker/bot/dates.db`).

      * *Note: Do not create a folder\! It must be a file.*

2.  **In Portainer Volumes:**

      * **Container Path:** `/app/dates.db`
      * **Host Path:** Select the `dates.db` file you just created on your NAS.

3.  **Deploy\!**

-----

## 📱 How to Use

1.  Create a Telegram Group with your partner.
2.  Add the Bot to the group.
3.  Type `/start` (or press the Menu button if available).
4.  **Set your Anniversary:** Click **➕ Add Date**, name it **"Anniversary"**, and set the date. This enables the "❤️ Our Journey" button.

### 💾 File Structure

  * `main.py`: The application code.
  * `.env`: Stores your API Token (Security).
  * `dates.db`: The database file (Auto-created).
  * `Dockerfile`: Instructions for building the container.
  * `requirements.txt`: List of python libraries.
