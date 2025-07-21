import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import telebot
from telebot import types
import requests
from bs4 import BeautifulSoup
from flask import Flask, request
import logging
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Bot Configuration
TOKEN = os.getenv('TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
TAMILMV_URL = os.getenv('TAMILMV_URL', 'https://www.1tamilmv.boo')
PORT = int(os.getenv('PORT', 3000))
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # Add admin ID to .env file

bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
app = Flask(__name__)

# Global variables
movie_cache = {}
last_cache_update = None
CACHE_DURATION = 300  # 5 minutes

def is_admin(user_id):
    """Check if user is admin"""
    return user_id == ADMIN_ID

def get_date_filter(days_ago=0):
    """Get date filter for posts"""
    target_date = datetime.now() - timedelta(days=days_ago)
    return target_date.strftime("%Y-%m-%d")

def parse_post_date(post_element):
    """Extract and parse date from post element"""
    try:
        # Look for time element or date info
        time_elem = post_element.find('time')
        if time_elem:
            date_str = time_elem.get('datetime') or time_elem.get('title')
            if date_str:
                # Parse various date formats
                for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]:
                    try:
                        return datetime.strptime(date_str.split('T')[0], fmt.split('T')[0])
                    except:
                        continue
        
        # Fallback: look for relative date text
        date_text = post_element.get_text()
        if 'today' in date_text.lower():
            return datetime.now()
        elif 'yesterday' in date_text.lower():
            return datetime.now() - timedelta(days=1)
            
    except Exception as e:
        logger.error(f"Error parsing post date: {e}")
    
    return datetime.now()  # Default to today

@bot.message_handler(commands=['start'])
def start_command(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 <b>Access Denied</b>\n\nThis bot is restricted to admin use only.")
        return
    
    text_message = """<b>🎬 TamilMV Bot - Admin Panel</b>

<blockquote><b>Get latest movies from 1TamilMV</b></blockquote>

<b>📋 Available Commands:</b>

🔹 <b>/today</b> - Get today's latest posts
🔹 <b>/yesterday</b> - Get yesterday's posts  
🔹 <b>/date</b> - Get posts from specific date
🔹 <b>/getlink</b> - Extract torrent links from URL
🔹 <b>/search</b> - Search for specific movie
🔹 <b>/stats</b> - View bot statistics

<blockquote><b>⚡ Admin Access Only</b></blockquote>"""

    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("📅 Today", callback_data="today"),
        types.InlineKeyboardButton("📆 Yesterday", callback_data="yesterday")
    )
    keyboard.row(
        types.InlineKeyboardButton("🔍 Search", callback_data="search"),
        types.InlineKeyboardButton("📊 Stats", callback_data="stats")
    )
    
    bot.send_message(
        chat_id=message.chat.id,
        text=text_message,
        reply_markup=keyboard
    )

@bot.message_handler(commands=['today'])
def today_posts(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 Access Denied")
        return
    
    bot.send_message(message.chat.id, "🔄 <b>Fetching today's posts...</b>")
    movies = fetch_movies_by_date(0)  # 0 days ago = today
    send_movie_list(message.chat.id, movies, "📅 Today's Posts")

@bot.message_handler(commands=['yesterday'])
def yesterday_posts(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 Access Denied")
        return
    
    bot.send_message(message.chat.id, "🔄 <b>Fetching yesterday's posts...</b>")
    movies = fetch_movies_by_date(1)  # 1 day ago = yesterday
    send_movie_list(message.chat.id, movies, "📆 Yesterday's Posts")

@bot.message_handler(commands=['date'])
def specific_date(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 Access Denied")
        return
    
    msg = bot.send_message(
        message.chat.id, 
        "📅 <b>Enter date in format:</b>\n\n<code>YYYY-MM-DD</code>\n\nExample: <code>2024-01-15</code>"
    )
    bot.register_next_step_handler(msg, process_date_input)

def process_date_input(message):
    try:
        date_str = message.text.strip()
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
        days_ago = (datetime.now() - target_date).days
        
        if days_ago < 0:
            bot.send_message(message.chat.id, "⚠️ Cannot fetch future dates!")
            return
        
        bot.send_message(message.chat.id, f"🔄 <b>Fetching posts for {date_str}...</b>")
        movies = fetch_movies_by_date(days_ago)
        send_movie_list(message.chat.id, movies, f"📅 Posts for {date_str}")
        
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid date format! Use YYYY-MM-DD")

@bot.message_handler(commands=['search'])
def search_command(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 Access Denied")
        return
    
    msg = bot.send_message(message.chat.id, "🔍 <b>Enter movie name to search:</b>")
    bot.register_next_step_handler(msg, process_search)

def process_search(message):
    search_term = message.text.strip().lower()
    bot.send_message(message.chat.id, f"🔍 <b>Searching for:</b> {message.text}")
    
    movies = fetch_all_movies()
    filtered_movies = {k: v for k, v in movies.items() if search_term in k.lower()}
    
    if filtered_movies:
        send_movie_list(message.chat.id, filtered_movies, f"🔍 Search Results for '{message.text}'")
    else:
        bot.send_message(message.chat.id, f"❌ No movies found for '<b>{message.text}</b>'")

@bot.message_handler(commands=['getlink'])
def get_link_command(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 Access Denied")
        return
    
    msg = bot.send_message(
        message.chat.id, 
        "🔗 <b>Send the post URL to extract torrent links</b>\n\nExample: https://www.1tamilmv.boo/index.php?/topic/12345"
    )
    bot.register_next_step_handler(msg, process_url_step)

def process_url_step(message):
    try:
        post_url = message.text.strip()
        
        if not post_url.startswith('http'):
            bot.send_message(message.chat.id, "❌ Invalid URL format!")
            return

        status_msg = bot.send_message(message.chat.id, "🔄 <b>Extracting torrent links...</b>")
        
        movie_details = get_movie_details_from_url(post_url)
        
        bot.delete_message(message.chat.id, status_msg.message_id)
        
        if movie_details:
            bot.send_message(message.chat.id, f"✅ <b>Found {len(movie_details)} torrent links:</b>\n")
            for detail in movie_details:
                bot.send_message(message.chat.id, text=detail, disable_web_page_preview=True)
        else:
            bot.send_message(message.chat.id, "❌ No torrent links found in the provided URL")
            
    except Exception as e:
        logger.error(f"Error processing URL: {e}")
        bot.send_message(message.chat.id, "❌ Error processing URL. Please try again.")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 Access Denied")
        return
    
    try:
        total_movies = len(movie_cache)
        cache_age = "No cache" if not last_cache_update else f"{int((time.time() - last_cache_update) / 60)} minutes ago"
        
        stats_text = f"""📊 <b>Bot Statistics</b>

🎬 <b>Cached Movies:</b> {total_movies}
🕐 <b>Last Update:</b> {cache_age}
🌐 <b>Source:</b> 1TamilMV
👤 <b>Admin ID:</b> {ADMIN_ID}

<blockquote><b>Cache refreshes every 5 minutes</b></blockquote>"""
        
        bot.send_message(message.chat.id, stats_text)
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        bot.send_message(message.chat.id, "❌ Error getting statistics")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Access Denied")
        return
    
    if call.data == "today":
        bot.answer_callback_query(call.id, "Fetching today's posts...")
        movies = fetch_movies_by_date(0)
        send_movie_list(call.message.chat.id, movies, "📅 Today's Posts")
        
    elif call.data == "yesterday":
        bot.answer_callback_query(call.id, "Fetching yesterday's posts...")
        movies = fetch_movies_by_date(1)
        send_movie_list(call.message.chat.id, movies, "📆 Yesterday's Posts")
        
    elif call.data == "search":
        bot.answer_callback_query(call.id, "Search mode activated")
        msg = bot.send_message(call.message.chat.id, "🔍 <b>Enter movie name to search:</b>")
        bot.register_next_step_handler(msg, process_search)
        
    elif call.data == "stats":
        bot.answer_callback_query(call.id, "Getting statistics...")
        stats_command(call.message)
        
    elif call.data.startswith("movie_"):
        movie_index = int(call.data.split("_")[1])
        movie_titles = list(movie_cache.keys())
        
        if 0 <= movie_index < len(movie_titles):
            movie_title = movie_titles[movie_index]
            bot.answer_callback_query(call.id, f"Getting links for {movie_title}")
            
            if movie_title in movie_cache:
                for detail in movie_cache[movie_title]:
                    bot.send_message(call.message.chat.id, text=detail, disable_web_page_preview=True)

def send_movie_list(chat_id, movies, title):
    if not movies:
        bot.send_message(chat_id, f"❌ No movies found for {title}")
        return
    
    text = f"<b>{title}</b>\n\n🔘 <b>Select a movie:</b> ({len(movies)} found)\n"
    
    keyboard = types.InlineKeyboardMarkup()
    for index, movie_title in enumerate(movies.keys()):
        keyboard.add(
            types.InlineKeyboardButton(
                text=f"🎬 {movie_title[:50]}...",
                callback_data=f"movie_{index}"
            )
        )
    
    bot.send_message(chat_id, text, reply_markup=keyboard)

def fetch_movies_by_date(days_ago=0):
    global movie_cache, last_cache_update
    
    # Check cache validity
    if last_cache_update and (time.time() - last_cache_update) < CACHE_DURATION:
        return filter_movies_by_date(movie_cache, days_ago)
    
    # Fetch fresh data
    movies = fetch_all_movies()
    return filter_movies_by_date(movies, days_ago)

def filter_movies_by_date(movies, days_ago):
    target_date = get_date_filter(days_ago)
    # For now, return all movies since date parsing from the site might be complex
    # You can implement actual date filtering based on the site's structure
    return movies

def fetch_all_movies():
    global movie_cache, last_cache_update
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(TAMILMV_URL, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        
        movies = {}
        posts = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if not posts:
            logger.warning("No posts found on the main page")
            return {}
        
        for post in posts[:25]:  # Limit to 25 posts for better performance
            try:
                title_tag = post.find('a')
                if title_tag:
                    title = title_tag.text.strip()
                    link = title_tag['href']
                    
                    if not link.startswith('http'):
                        link = f"{TAMILMV_URL}{link}"
                    
                    # Get movie details
                    movie_details = get_movie_details_from_url(link)
                    if movie_details:
                        movies[title] = movie_details
                        
            except Exception as e:
                logger.error(f"Error processing post: {e}")
                continue
        
        movie_cache = movies
        last_cache_update = time.time()
        
        logger.info(f"Cached {len(movies)} movies")
        return movies
        
    except Exception as e:
        logger.error(f"Error fetching movies: {e}")
        return movie_cache  # Return cached data if available

def get_movie_details_from_url(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        
        movie_details = []
        
        # Look for post content
        post_content = soup.find('div', class_='cPost_contentWrap')
        if not post_content:
            post_content = soup.find('div', class_='ipsType_richText')
        
        if not post_content:
            return []
        
        # Find torrent links
        torrent_links = post_content.find_all('a', {'data-fileext': 'torrent'})
        
        # If no direct torrent links, look for any links containing torrent-related keywords
        if not torrent_links:
            all_links = post_content.find_all('a', href=True)
            torrent_links = [link for link in all_links if 
                           'torrent' in link.get('href', '').lower() or 
                           'magnet:' in link.get('href', '').lower()]
        
        for link in torrent_links:
            try:
                torrent_url = link.get('href', '')
                torrent_name = link.text.strip() or 'Download Torrent'
                
                if not torrent_url:
                    continue
                
                # Handle relative URLs
                if torrent_url.startswith('/'):
                    torrent_url = f"{TAMILMV_URL}{torrent_url}"
                elif not torrent_url.startswith('http') and not torrent_url.startswith('magnet:'):
                    torrent_url = f"{TAMILMV_URL}/{torrent_url}"
                
                # Clean up the torrent name
                torrent_name = re.sub(r'\s+', ' ', torrent_name).strip()
                if len(torrent_name) > 100:
                    torrent_name = torrent_name[:97] + "..."
                
                # Format message
                message = f"📁 <b>{torrent_name}</b>\n\n🔗 <code>{torrent_url}</code>"
                movie_details.append(message)
                
            except Exception as e:
                logger.error(f"Error processing torrent link: {e}")
                continue
        
        # If no torrents found, look for any download links
        if not movie_details:
            download_links = post_content.find_all('a', href=True)
            for link in download_links[:3]:  # Max 3 fallback links
                href = link.get('href', '')
                text = link.text.strip()
                if href and text and len(text) > 5:
                    if not href.startswith('http'):
                        href = f"{TAMILMV_URL}/{href.lstrip('/')}"
                    message = f"🔗 <b>{text[:50]}</b>\n\n<code>{href}</code>"
                    movie_details.append(message)
        
        return movie_details[:5]  # Return max 5 links
        
    except Exception as e:
        logger.error(f"Error getting movie details from {url}: {e}")
        return []

# Flask routes
@app.route('/')
def health_check():
    return "TamilMV Bot - Admin Only", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        return 'Invalid content type', 403

if __name__ == "__main__":
    logger.info("Starting TamilMV Bot...")
    
    # Remove any previous webhook
    bot.remove_webhook()
    time.sleep(1)
    
    # Set webhook
    webhook_url = f"{WEBHOOK_URL}/webhook"
    bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set to: {webhook_url}")
    
    # Start Flask app
    logger.info(f"Starting Flask app on port {PORT}")
    app.run(host='0.0.0.0', port=PORT)
