from flask import Flask, request, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import pandas as pd
import random
import os
from werkzeug.utils import secure_filename
import json
import pylast

# Create the application instance
app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a secure key

# Configure the SQLite database - use absolute path for reliability
db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'users.db')
# Delete existing database if it exists to force recreation with new schema
if os.path.exists(db_path):
    try:
        os.remove(db_path)
        print(f"Removed existing database at {db_path}")
    except Exception as e:
        print(f"Could not remove existing database: {e}")

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configure upload settings
UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create the upload directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Make sure the static folder exists
os.makedirs(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static'), exist_ok=True)

# Print the database path to verify location
print(f"Database path: {db_path}")

# Initialize the database
db = SQLAlchemy(app)

# Initialize Flask-Login
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Set up Last.fm API
API_KEY = '2bbf4f41f9948d580841b4ad251a1e1c'  # Replace with your actual API key
API_SECRET = 'd8469f12eeca4373ac6815213c4727fc'  # Replace with your actual API secret

# Set up Last.fm network
network = pylast.LastFMNetwork(
    api_key=API_KEY,
    api_secret=API_SECRET
)


# Helper function for file uploads
def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    profile_pic = db.Column(db.String(255), default='default-profile.png')
    favorite_artist = db.Column(db.String(255), default='')
    favorite_song = db.Column(db.String(255), default='')
    favorite_genre = db.Column(db.String(255), default='')
    recent_vibes = db.Column(db.Text, default='[]')  # Store as JSON string

    def __repr__(self):
        return f'<User {self.username}>'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Create a default profile picture if it doesn't exist
default_pic_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static', 'default-profile.png')
if not os.path.exists(default_pic_path):
    # Create a simple placeholder image or copy from elsewhere
    try:
        # If you have PIL installed you could create an image, but for simplicity let's just create an empty file
        with open(default_pic_path, 'w') as f:
            f.write('placeholder')
        print(f"Created placeholder default profile picture at {default_pic_path}")
    except Exception as e:
        print(f"Could not create default profile picture: {e}")

# Create database tables
with app.app_context():
    try:
        db.create_all()
        print("Database tables created successfully")

        # Check if any users exist
        user_count = User.query.count()
        print(f"Number of users in database: {user_count}")

        # Create a test user if no users exist
        if user_count == 0:
            test_user = User(
                username="test",
                email="test@example.com",
                password="test",
                profile_pic="default-profile.png",
                favorite_artist="",
                favorite_song="",
                favorite_genre="",
                recent_vibes="[]"
            )
            db.session.add(test_user)
            try:
                db.session.commit()
                print("Test user created successfully!")
            except Exception as e:
                db.session.rollback()
                print(f"Error creating test user: {e}")
    except Exception as e:
        print(f"Error creating database tables: {e}")


# New Last.fm recommendation functions

def get_recommendations_by_song(track_name, artist_name=None, num_recommendations=10):
    """Find a song on Last.fm and recommend similar tracks with album artwork."""
    try:
        # If we have both track and artist, use them together
        if artist_name:
            track = network.get_track(artist_name, track_name)
        else:
            # Search for the track
            search_results = network.search_for_track("", track_name)
            matches = search_results.get_next_page()
            if not matches:
                return None  # No match found

            # Use the first match
            track = matches[0]

        # Get similar tracks - request more than needed to ensure unique results
        similar_tracks = track.get_similar(limit=num_recommendations * 2)

        # Track IDs to avoid duplicates
        seen_tracks = set()

        # Format the recommendations
        formatted_recs = []
        for similar in similar_tracks:
            if len(formatted_recs) >= num_recommendations:
                break

            track_obj = similar.item
            artist_obj = track_obj.get_artist()

            # Create a unique ID for this track
            track_id = f"{track_obj.get_name().lower()}_{artist_obj.get_name().lower()}"

            # Skip if we've already seen this track
            if track_id in seen_tracks:
                continue

            seen_tracks.add(track_id)

            # Try to get album information and cover art
            album_image = None
            try:
                # Get top albums from this artist
                top_albums = artist_obj.get_top_albums(limit=3)
                if top_albums:
                    # Use the first album's image
                    album = top_albums[0].item
                    album_image = album.get_cover_image(size=3)  # Size 3 is medium sized image
            except:
                # Fallback if we can't get album image
                album_image = None

            formatted_recs.append({
                'track_name': track_obj.get_name(),
                'artist_name': artist_obj.get_name(),
                'genre': 'Unknown',
                'url': track_obj.get_url(),
                'image_url': album_image
            })

        return formatted_recs
    except pylast.WSError as e:
        print(f"Last.fm API error: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def get_recommendations_by_artist(artist_name, num_recommendations=10):
    """Find an artist on Last.fm and recommend similar artists with better image handling."""
    try:
        artist = network.get_artist(artist_name)

        # Get similar artists - request more than needed to ensure unique results
        similar_artists = artist.get_similar(limit=num_recommendations * 2)

        # Artist IDs to avoid duplicates
        seen_artists = set()

        # Format the results
        formatted_recs = []
        for similar in similar_artists:
            if len(formatted_recs) >= num_recommendations:
                break

            artist_obj = similar.item

            # Create a unique ID for this artist
            artist_id = artist_obj.get_name().lower()

            # Skip if we've already seen this artist
            if artist_id in seen_artists:
                continue

            seen_artists.add(artist_id)

            # Try to get top tags (genres) for this artist
            try:
                top_tags = artist_obj.get_top_tags(limit=1)
                genre = top_tags[0].item.get_name() if top_tags else 'Unknown'
            except:
                genre = 'Unknown'

            # Try multiple approaches to get artist image
            artist_image = None

            # First try to get artist image directly
            try:
                artist_image = artist_obj.get_cover_image(size=3)
            except:
                artist_image = None

            # If direct method fails, try getting from their top album
            if not artist_image:
                try:
                    top_albums = artist_obj.get_top_albums(limit=1)
                    if top_albums and len(top_albums) > 0:
                        album = top_albums[0].item
                        artist_image = album.get_cover_image(size=3)
                except:
                    pass

            # If that fails, try searching for the artist
            if not artist_image:
                try:
                    search_results = network.search_for_artist(artist_obj.get_name())
                    first_match = search_results.get_next_page()[0].item
                    artist_image = first_match.get_cover_image(size=3)
                except:
                    artist_image = None

            formatted_recs.append({
                'name': artist_obj.get_name(),
                'genre': genre,
                'url': artist_obj.get_url(),
                'image_url': artist_image
            })

        return formatted_recs
    except pylast.WSError as e:
        print(f"Last.fm API error: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

def recommend_music(song, artist):
    """Process user input and return stylized music recommendations with images."""
    result_html = ""

    if song:
        recommendations = get_recommendations_by_song(song, artist, num_recommendations=10)

        if recommendations:
            result_html += f"""
            <div class="recommendation-header">
                <h2>Based on <span class="highlight">{song}</span></h2>
                <p class="rec-subheading">You might enjoy these tracks:</p>
            </div>
            """
            result_html += '<div class="recommendation-grid">'

            for rec in recommendations:
                image_url = rec.get('image_url') or '/static/default-album.svg'  # Use Maestro logo if no image

                result_html += f'''
                <div class="recommendation-card">
                    <div class="album-cover">
                        <img src="{image_url}" alt="Album cover for {rec['track_name']}">
                        <div class="play-overlay">
                            <i class="fas fa-play-circle"></i>
                        </div>
                    </div>
                    <div class="rec-info">
                        <h3 class="song-title">{rec['track_name']}</h3>
                        <p class="artist-name">{rec['artist_name']}</p>
                        <a href="{rec['url']}" target="_blank" class="lastfm-link">
                            <i class="fas fa-external-link-alt"></i> Last.fm
                        </a>
                    </div>
                </div>
                '''

            result_html += '</div>'
            return result_html

    if artist:
        artist_recommendations = get_recommendations_by_artist(artist, num_recommendations=10)

        if artist_recommendations:
            result_html += f"""
            <div class="recommendation-header">
                <h2>Based on <span class="highlight">{artist}</span></h2>
                <p class="rec-subheading">You might enjoy these artists:</p>
            </div>
            """
            result_html += '<div class="recommendation-grid">'

            for rec in artist_recommendations:
                image_url = rec.get('image_url') or '/static/default-artist.svg'  # Use Maestro logo if no image

                result_html += f'''
                <div class="recommendation-card">
                    <div class="artist-image">
                        <img src="{image_url}" alt="Image of {rec['name']}">
                        <div class="play-overlay">
                            <i class="fas fa-play-circle"></i>
                        </div>
                    </div>
                    <div class="rec-info">
                        <h3 class="artist-title">{rec['name']}</h3>
                        <p class="genre-name">Genre: {rec['genre']}</p>
                        <a href="{rec['url']}" target="_blank" class="lastfm-link">
                            <i class="fas fa-external-link-alt"></i> Last.fm
                        </a>
                    </div>
                </div>
                '''

            result_html += '</div>'
            return result_html

    return "No recommendations found. Try another song or artist."
# Routes for authentication
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        print(f"Signup attempt: {username}, {email}")

        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists!', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already exists!', 'error')
        else:
            # Create a new user
            new_user = User(username=username, email=email, password=password)  # In a real app, hash the password!
            db.session.add(new_user)
            try:
                db.session.commit()
                print(f"User {username} created successfully!")
                flash('Account created successfully!', 'success')
                return redirect(url_for('login'))
            except Exception as e:
                db.session.rollback()
                print(f"Error creating user: {e}")
                flash('Error creating account. Please try again.', 'error')

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    # If user is already logged in, redirect to index
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Debug: print the submitted credentials
        print(f"Login attempt: {username}, {password}")

        user = User.query.filter_by(username=username).first()

        # Debug: print if user was found
        print(f"User found: {user is not None}")
        if user:
            print(f"User details: {user.username}, {user.email}")

        if user and user.password == password:  # In a real app, verify hashed password!
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password!', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))  # Changed from 'home' to 'login'


# Profile page
@app.route('/profile')
@login_required
def profile():
    recent_vibes = []
    try:
        recent_vibes = json.loads(current_user.recent_vibes)
    except:
        pass

    # Verify if the profile pic exists, if not, reset to default
    if current_user.profile_pic != 'default-profile.png':
        pic_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.profile_pic)
        if not os.path.exists(pic_path):
            current_user.profile_pic = 'default-profile.png'
            try:
                db.session.commit()
            except:
                db.session.rollback()

    return render_template('profile.html', user=current_user, recent_vibes=recent_vibes)


# Profile update route
@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    # Handle profile picture upload
    if 'profile_pic' in request.files:
        file = request.files['profile_pic']
        if file and file.filename != '' and allowed_file(file.filename):
            # Delete previous profile pic if it's not the default
            if current_user.profile_pic != 'default-profile.png':
                try:
                    old_pic_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.profile_pic)
                    if os.path.exists(old_pic_path):
                        os.remove(old_pic_path)
                except Exception as e:
                    print(f"Error removing old profile pic: {e}")

            # Save new profile pic
            filename = secure_filename(file.filename)
            # Add username prefix to avoid filename conflicts
            unique_filename = f"{current_user.username}_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            current_user.profile_pic = unique_filename

    # Update favorites
    favorite_artist = request.form.get('favorite_artist')
    favorite_song = request.form.get('favorite_song')
    favorite_genre = request.form.get('favorite_genre')

    if favorite_artist:
        current_user.favorite_artist = favorite_artist
    if favorite_song:
        current_user.favorite_song = favorite_song
    if favorite_genre:
        current_user.favorite_genre = favorite_genre

    # Handle recently vibed with
    recent_vibe = request.form.get('recent_vibe')
    if recent_vibe:
        # Get current recent vibes list
        try:
            recent_vibes = json.loads(current_user.recent_vibes)
        except:
            recent_vibes = []

        # Add new vibe to the beginning of the list
        recent_vibes.insert(0, recent_vibe)

        # Keep only the latest 5 vibes
        recent_vibes = recent_vibes[:5]

        # Save back to the database
        current_user.recent_vibes = json.dumps(recent_vibes)

    # Save all changes to the database
    try:
        db.session.commit()
        flash('Profile updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating profile: {e}', 'error')

    return redirect(url_for('profile'))


# Main index route
@app.route('/', methods=['GET', 'POST'])
def home():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('home.html')


@app.route('/index', methods=['GET', 'POST'])
@login_required
def index():
    output = None
    if request.method == 'POST':
        # Get inputs from the HTML form
        song = request.form.get('song', '').strip()
        artist = request.form.get('artist', '').strip()

        if not song and not artist:
            output = "Please enter a song or an artist."
        else:
            try:
                output = recommend_music(song, artist)
            except Exception as e:
                output = f"Error getting recommendations: {str(e)}"
                print(f"Last.fm API error: {e}")

    # Render the HTML template with the output
    return render_template('index.html', output=output)


if __name__ == '__main__':
    app.run(debug=True)