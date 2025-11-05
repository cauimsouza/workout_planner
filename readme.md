# Workout Tracker with HTMX

A simple workout tracking application built with FastAPI and HTMX.

## Features

- **Log Workouts**: Record exercises with reps, weight, and RPE (Rate of Perceived Exertion)
- **View History**: See all your past workouts in a clean, organized interface
- **Real-time Updates**: HTMX provides dynamic updates without page reloads

## Tech Stack

- **Backend**: FastAPI + SQLModel
- **Frontend**: HTMX + Vanilla CSS
- **Database**: SQLite

## Setup in GitHub Codespaces

### 1. Open in Codespaces

Create a new Codespace from your repository. The environment will automatically set up Python.

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the Application

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

The `--host 0.0.0.0` flag is important for Codespaces to properly forward the port.

### 4. Access the Application

Once the server starts:
1. GitHub Codespaces will show a notification about port 8000
2. Click "Open in Browser" or go to the "Ports" tab
3. Click the local address for port 8000

Alternatively, you can manually forward the port:
- Press `Ctrl/Cmd + Shift + P`
- Type "Forward a Port"
- Enter `8000`
- Click the globe icon to open in browser

## Project Structure

```
.
├── app.py           # FastAPI application with HTMX endpoints
├── models.py        # SQLModel database models
├── database.py      # Database configuration and seeding
├── index.html       # HTMX frontend
├── requirements.txt # Python dependencies
└── README.md        # This file
```

## API Endpoints

### Web Interface
- `GET /` - Serves the HTML interface

### HTMX Endpoints (return HTML fragments)
- `POST /workouts/` - Create a new workout (returns HTML fragment)
- `GET /workouts` - Get all workouts (returns HTML list)
- `GET /exercises` - Get exercise options (returns HTML options)

## Usage

### Adding a Workout

1. Select an exercise from the dropdown
2. Enter the number of reps
3. Enter the weight in kilograms
4. Enter your RPE (1-10 scale)
5. Click "Log Workout"

The new workout will appear at the top of your history instantly!

### Understanding RPE

RPE (Rate of Perceived Exertion) is a scale from 1-10:
- **1-3**: Very light effort
- **4-6**: Moderate effort
- **7-8**: Hard effort, could do 2-3 more reps
- **9**: Very hard, maybe 1 more rep
- **10**: Maximum effort

## Adding More Exercises

You can add more exercises by modifying the `seed_db()` function in `database.py`:

```python
exercises = [
    Exercise(name='Pull-ups'),
    Exercise(name='Dips'),
    Exercise(name='Squats'),       # Add new exercises here
    Exercise(name='Bench Press'),
    Exercise(name='Deadlifts'),
]
```

Then delete `database.db` and restart the server to reseed the database.

## Development Tips for Codespaces

### Auto-reload on Changes
The `--reload` flag enables auto-reload when you save files.

### Viewing Logs
FastAPI logs will appear in the terminal. Set `echo=True` in `database.py` to see SQL queries.

### Debugging
You can use breakpoints in VS Code. Click to the left of line numbers to add breakpoints, then use the debugger.

### Database Management
The SQLite database (`database.db`) is created automatically. You can inspect it with:

```bash
sqlite3 database.db
```

Useful SQLite commands:
```sql
.tables                 -- List all tables
SELECT * FROM exercise; -- View exercises
SELECT * FROM workout;  -- View workouts
```

## Customization

### Styling
All CSS is in `index.html`. Modify the `<style>` section to change colors, layouts, etc.

### Adding Features
Some ideas:
- Add date tracking
- Filter workouts by exercise
- Display workout statistics
- Export workout data
- Add sets tracking (multiple sets per workout)

## Troubleshooting

### Port Already in Use
If port 8000 is busy, use a different port:
```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8080
```

### Database Issues
Delete `database.db` to reset:
```bash
rm database.db
```

### Import Errors
Make sure you're in the project directory and have installed requirements:
```bash
pip install -r requirements.txt --break-system-packages
```

## License

MIT License - feel free to use and modify!