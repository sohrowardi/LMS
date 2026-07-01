# How It Works

1. Install the required packages:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install fastapi uvicorn[standard] httpx beautifulsoup4
   ```

2. Run the crawler once to build the local search database (this may take a few minutes):
   ```bash
   python3 crawler.py
   ```

3. Start the app:
   ```bash
   python3 app.py
   ```
   **or**
   ```bash
   ./start.sh
   ```

4. Your browser will open automatically. If you skipped step 2, you can click **Re-index Now** in the app to build the database later.

Once the database is built, all searches are performed locally, making results nearly instant.