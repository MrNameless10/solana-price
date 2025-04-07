import os
import discord
from discord.ext import commands, tasks
import requests
import logging
import asyncio
import json  # For data persistence
import datetime
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)


# Set up intents
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.guild_messages = True

# Create the bot client with the desired command prefix
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command('help')  # Remove default help command to implement a custom one

# --- Data Persistence for Alert Settings ---
alert_settings = {}  # Dictionary to store guild IDs and their settings

def save_alert_settings():
    with open('alert_settings.json', 'w') as f:
        json.dump(alert_settings, f)

def load_alert_settings():
    global alert_settings
    try:
        with open('alert_settings.json', 'r') as f:
            alert_settings = json.load(f)
            # Convert keys back to integers
            alert_settings = {int(k): v for k, v in alert_settings.items()}
    except FileNotFoundError:
        alert_settings = {}

load_alert_settings()

# Initialize previous_price for price-change tracking
previous_price = None

# --- Helper Function for Cryptocurrency Price ---
def get_crypto_price(coin_id):
    """Fetches the current price and 24h percentage change of a cryptocurrency from CoinGecko API."""
    try:
        response = requests.get(
            f'https://api.coingecko.com/api/v3/coins/{coin_id}',
            params={
                'localization': 'false',
                'tickers': 'false',
                'market_data': 'true',
                'community_data': 'false',
                'developer_data': 'false',
                'sparkline': 'false'
            })
        data = response.json()
        current_price = data['market_data']['current_price']['usd']
        percentage_change_24h = data['market_data']['price_change_percentage_24h']
        return current_price, percentage_change_24h
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error: {e}")
        return None, None
    except KeyError as e:
        logging.error(f"Key error: {e}")
        return None, None
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return None, None

# --- New Helper Function for Play Solana NFT Stats ---
def get_play_solana_stats():
    """Fetches stats for the play_solana NFT collection from Magic Eden API and converts prices to SOL."""
    try:
        response = requests.get("https://api-mainnet.magiceden.dev/v2/collections/play_solana/stats")
        data = response.json()
        # Convert lamports to SOL (1 SOL = 10^9 lamports)
        floor_sol = data.get("floorPrice", 0) / 1e9
        avg_price_sol = data.get("avgPrice24hr", 0) / 1e9
        volume_sol = data.get("volumeAll", 0) / 1e9
        listed_count = data.get("listedCount", 0)
        return floor_sol, avg_price_sol, listed_count, volume_sol
    except Exception as e:
        logging.error(f"Error fetching play_solana stats: {e}")
        return None, None, None, None

# Global toggle to alternate between displaying Solana price and NFT stats
toggle = True

# --- Background Task to Update Bot Nickname and Presence ---
@tasks.loop(seconds=30)
async def update_price():
    global previous_price, toggle
    current_price, percentage_change_24h = get_crypto_price('solana')
    floor_sol, avg_price_sol, listed_count, volume_sol = get_play_solana_stats()

    if current_price is None or floor_sol is None:
        status_text = "Price unavailable"
    else:
        if toggle:
            # Display Solana price only
            percentage_change_24h = round(percentage_change_24h, 2)
            if percentage_change_24h > 0:
                emoji = '↗'
                sign = '+'
            elif percentage_change_24h < 0:
                emoji = '↘'
                sign = ''
            else:
                emoji = '➡️'
                sign = ''
            status_text = f"Sol: ${current_price:.2f} {emoji} ({sign}{percentage_change_24h}%)"
        else:
            # Display play_solana NFT stats with floor price in SOL and USD, plus listed count
            floor_usd = floor_sol * current_price
            status_text = f"NFT: Floor {floor_sol:.2f} SOL (~${floor_usd:.2f}), {listed_count} listed"

    # Update the bot's nickname in all guilds (if permissions allow)
    for guild in bot.guilds:
        try:
            await guild.me.edit(nick=status_text)
        except Exception as e:
            logging.error(f"Could not update nickname in guild {guild.name}: {e}")

    # Optionally update the bot's presence activity too
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=status_text))

    # Toggle for the next iteration
    toggle = not toggle

# --- Event: Bot Ready ---
@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user}')
    update_price.start()  # Start the background task to update price

    # Fetch the target guild and channel from environment variables
    target_guild_id = int(os.getenv('TARGET_GUILD_ID'))  # Your server ID
    target_channel_id = int(os.getenv('TARGET_CHANNEL_ID'))  # Your channel ID

    # Prepare the log message with the list of servers
    guilds_info = "\n".join([f"- {guild.name} (ID: {guild.id})" for guild in bot.guilds])
    log_message = (
        f"Hello! The bot is now online and ready to assist you.\n\n"
        f"**Current Servers:**\n{guilds_info}\n\n"
        f"Total Servers: {len(bot.guilds)}"
    )

    try:
        guild = bot.get_guild(target_guild_id)
        if guild:
            channel = guild.get_channel(target_channel_id)
            if channel:
                await channel.send(log_message)  # Send the log message
                logging.info(f"Sent startup log to {guild.name} in {channel.name}")
            else:
                logging.warning("Target channel not found.")
        else:
            logging.warning("Target guild not found.")
    except Exception as e:
        logging.error(f"Error sending startup log message: {e}")

    # Sync application commands (slash commands)
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} command(s).")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")

# --- Slash Command: Setup Alerts ---
@bot.tree.command(name="setup-alerts", description="Set up price alerts for Solana.")
@discord.app_commands.describe(threshold="The percentage change threshold for alerts (e.g., 1.0 for 1%)")
async def setup_warnings(interaction: discord.Interaction, threshold: float = 1.0):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild

    if not guild.me.guild_permissions.manage_channels:
        await interaction.followup.send("I need the 'Manage Channels' permission to set up warnings.", ephemeral=True)
        return

    # Create or get a category for alerts
    category_name = "Solana Alerts"
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name)

    # Create or get a text channel for price alerts
    existing_channel = discord.utils.get(category.channels, name="price-alerts")
    if existing_channel is None:
        channel = await guild.create_text_channel("price-alerts", category=category)
    else:
        channel = existing_channel

    # Save alert settings for the guild
    guild_id = guild.id
    alert_settings[guild_id] = {
        'channel_id': channel.id,
        'threshold': threshold
    }
    save_alert_settings()

    await interaction.followup.send(
        f"Price alerts will be sent to {channel.mention} when Solana's price changes by {threshold}% or more.",
        ephemeral=True)

# --- Slash Command: Get Solana Price ---
@bot.tree.command(name="sol", description="Get the current Solana price.")
async def sol(interaction: discord.Interaction):
    price, percentage_change = get_crypto_price('solana')
    if price is not None and percentage_change is not None:
        percentage_change = round(percentage_change, 2)
        embed = discord.Embed(
            title="Solana Price",
            description=(f"The current Solana price is **${price:.2f}**\n"
                         f"24h Change: {percentage_change}%"),
            color=discord.Color.gold())
        embed.set_author(
            name="Powered by Play Solana Team",
            url="https://www.playsolana.com/",
            icon_url="https://pbs.twimg.com/profile_images/1824202337553952768/c7AGrGNp_400x400.jpg"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("Sorry, I couldn't fetch the Solana price right now.", ephemeral=True)

# --- Slash Command: Get Price of Any Cryptocurrency ---
@bot.tree.command(name="price", description="Get the current price of a cryptocurrency.")
async def price(interaction: discord.Interaction, coin: str):
    coin = coin.lower()
    current_price, _ = get_crypto_price(coin)
    if current_price is not None:
        embed = discord.Embed(
            title=f"{coin.capitalize()} Price",
            description=f"The current price of {coin.capitalize()} is **${current_price:.2f}**",
            color=discord.Color.blue())
        embed.set_author(
            name="Powered by Play Solana Team",
            url="https://www.playsolana.com/",
            icon_url="https://pbs.twimg.com/profile_images/1824202337553952768/c7AGrGNp_400x400.jpg"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"Sorry, I couldn't find the price for '{coin}'.", ephemeral=True)

# --- Slash Command: Get Market Data ---
@bot.tree.command(name="market", description="Get market data for a cryptocurrency.")
async def market(interaction: discord.Interaction, coin: str):
    coin = coin.lower()
    try:
        await interaction.response.defer(ephemeral=True)
        response = requests.get(f'https://api.coingecko.com/api/v3/coins/{coin}')
        data = response.json()
        if 'market_data' in data:
            market_data = data['market_data']
            current_price = market_data['current_price']['usd']
            market_cap = market_data['market_cap']['usd']
            volume = market_data['total_volume']['usd']
            embed = discord.Embed(title=f"{data['name']} Market Data", color=discord.Color.green())
            embed.add_field(name="Price", value=f"**${current_price:,.2f}**", inline=False)
            embed.add_field(name="Market Cap", value=f"${market_cap:,.2f}", inline=False)
            embed.add_field(name="24h Volume", value=f"${volume:,.2f}", inline=False)
            embed.set_author(
                name="Powered by Play Solana Team",
                url="https://www.playsolana.com/",
                icon_url="https://pbs.twimg.com/profile_images/1824202337553952768/c7AGrGNp_400x400.jpg"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(f"Sorry, market data for '{coin}' is unavailable.", ephemeral=True)
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error: {e}")
        await interaction.followup.send("Network error occurred.", ephemeral=True)
    except Exception as e:
        logging.error(f"Error fetching market data for {coin}: {e}")
        await interaction.followup.send("Sorry, there was an error fetching the market data.", ephemeral=True)

# --- Slash Command: Custom Help ---
@bot.tree.command(name="help", description="List available commands.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Help - Available Commands",
        description="Here are the commands you can use:",
        color=discord.Color.blue()
    )
    embed.add_field(name="/sol", value="Get the current Solana price.", inline=False)
    embed.add_field(name="/price <coin>", value="Get the current price of any cryptocurrency. Example: `/price bitcoin`", inline=False)
    embed.add_field(name="/market <coin>", value="Get detailed market data for a cryptocurrency. Example: `/market ethereum`", inline=False)
    embed.set_author(
        name="Powered by Play Solana Team",
        url="https://www.playsolana.com/",
        icon_url="https://pbs.twimg.com/profile_images/1824202337553952768/c7AGrGNp_400x400.jpg"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- Error Handling for Slash Commands ---
@bot.event
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, commands.errors.CommandNotFound):
        await interaction.response.send_message("Sorry, I didn't understand that command.", ephemeral=True)
    else:
        logging.error(f"An error occurred: {error}")
        await interaction.response.send_message("An unexpected error occurred.", ephemeral=True)

# --- Run the Bot ---
bot.run(os.getenv('DISCORD_TOKEN'))
