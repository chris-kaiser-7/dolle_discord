import discord
from discord.ext import commands
from openai import OpenAI
import pymongo
import time
from decouple import config
from io import BytesIO
import requests
from datetime import datetime

# Load environment variables
DISCORD_TOKEN = config("DISC_TOKEN")
OPENAI_API_KEY = config('OPENAI_API_KEY')
ATLAS_URI = config("ATLAS_URI")

# MongoDB setup
client = pymongo.MongoClient(ATLAS_URI)
db = client["dolle"]
images_collection = db["images"]
rate_limits_collection = db["rate_limits"]

# Rate limit defaults (per week)
DEFAULT_SERVER_LIMIT = 20
DEFAULT_USER_LIMIT = 10
custom_user_limits = {  
    317413000548188160: 40
}
custom_server_limits = { 
    1229813598033936445: 40
}

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

@bot.event
async def on_ready():
    global total_size
    print(f'{bot.user} has connected to Discord!')

def check_rate_limit(ctx):
    # Get or create rate limit documents
    server_id = str(ctx.guild.id)  # Store server ID as a string
    user_id = str(ctx.author.id)
    server_doc = rate_limits_collection.find_one_and_update(
        {"_id": server_id},
        {"$setOnInsert": {"count": 0, "reset_time": time.time() + 604800}},  # 1 week
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER
    )
    user_doc = rate_limits_collection.find_one_and_update(
        {"_id": user_id},
        {"$setOnInsert": {"count": 0, "reset_time": time.time() + 604800}},
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER
    )

    user_limit = custom_user_limits.get(ctx.author.id, DEFAULT_USER_LIMIT)
    server_limit = custom_server_limits.get(ctx.guild.id, DEFAULT_SERVER_LIMIT)

    # Check if rate limits are exceeded
    if server_doc["count"] >= server_limit and time.time() < server_doc["reset_time"]:
        return "Server rate limit exceeded."
    if user_doc["count"] >= user_limit and time.time() < user_doc["reset_time"]:
        return "User rate limit exceeded."

    # Increment counts
    rate_limits_collection.update_one({"_id": server_id}, {"$inc": {"count": 1}})
    rate_limits_collection.update_one({"_id": user_id}, {"$inc": {"count": 1}})
    return None

@bot.command(name="dolle")
async def generate(ctx, *, prompt):
    # Check rate limits
    rate_limit_error = check_rate_limit(ctx)
    if rate_limit_error:
        await ctx.send(rate_limit_error)
        return
    # Rest of the function (generating, logging, storing) remains the same
    generating_msg = await ctx.send(f"Generating image with prompt: \"{prompt}\"")

    # Log to console
    print(f"User: {ctx.author.name}, Prompt: {prompt}")

    try:
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="standard",
        )

        image_url = response.data[0].url
        response = requests.get(image_url)
        image = BytesIO(response.content)

        message = await ctx.send(file=discord.File(image, filename=f'{prompt}.png'))

        # Get the CDN URL from the message
        cdn_url = message.attachments[0].url

        # Store the CDN URL, user, timestamp, image hash, server, channel, and thumbnail in the database
        images_collection.insert_one({
            'cdn_url': cdn_url,
            'user_id': ctx.author.id,
            "user_name": ctx.author.name,
            'timestamp': datetime.now(),
            'prompt': prompt,
            'server': ctx.guild.name,
            'channel': ctx.channel.name,
        })

        # Log to console (with image URL)
        print(f"Image URL: {image_url}")
    except Exception as e:
        # Handle OpenAI API errors
        error_msg = f"Error generating image: {e}"
        await ctx.send(error_msg)
        print(error_msg)

    # Delete generating message
    await generating_msg.delete()

@bot.command(name="usage")
async def check_user_usage(ctx):
    user_id = str(ctx.author.id)
    user_doc = rate_limits_collection.find_one({"_id": user_id})

    if user_doc:
        user_limit = custom_user_limits.get(ctx.author.id, DEFAULT_USER_LIMIT)
        remaining = max(0, user_limit - user_doc["count"])
        reset_time = user_doc["reset_time"]
        time_left = int(reset_time - time.time())

        message = f"You have used {user_doc['count']} out of {user_limit} image requests this week.\n"
        message += f"You have {remaining} requests remaining.\n"
        message += f"Time until reset: {time_left // 3600} hours, {(time_left % 3600) // 60} minutes" 
    else:
        message = "You haven't used any image requests yet this week."

    await ctx.send(message)

@bot.command(name="server_usage")
async def check_server_usage(ctx):
    server_id = str(ctx.guild.id)
    server_doc = rate_limits_collection.find_one({"_id": server_id})

    if server_doc:
        server_limit = custom_server_limits.get(ctx.guild.id, DEFAULT_SERVER_LIMIT)
        remaining = max(0, server_limit - server_doc["count"])
        reset_time = server_doc["reset_time"]
        time_left = int(reset_time - time.time())

        message = f"This server has used {server_doc['count']} out of {server_limit} image requests this week.\n"
        message += f"There are {remaining} requests remaining.\n"
        message += f"Time until reset: {time_left // 3600} hours, {(time_left % 3600) // 60} minutes" 
    else:
        message = "This server hasn't used any image requests yet this week."

    await ctx.send(message)

@bot.command(name='portfolio')
async def get_portfolio(ctx, *args):
    # Find all images saved by the user
    query = {'user_id': ctx.author.id}
    if '-same-server' in args:
        query['server'] = ctx.guild.name
    elif '-server' in args:
        server_name = args[args.index('-server') + 1]
        query['server'] = server_name
    if '-same-channel' in args:
        query['channel'] = ctx.channel.name
    elif '-channel' in args:
        channel_name = args[args.index('-channel') + 1]
        query['channel'] = channel_name

    images = images_collection.find(query)

    # Send the CDN URL for each image
    for image in images:
        await ctx.send(image['cdn_url'])

bot.run(DISCORD_TOKEN)