import json
from json.decoder import JSONDecodeError
import os
from os.path import dirname as up
import string
import requests_cache
from sqlite3 import connect, Row

import datetime as dt
import logging
import configparser

from pymysql.err import OperationalError

# Annotation imports
from typing import (
    Any,
    Optional,
    Dict,
    List
)

# Discord imports
import discord
from discord.ext import commands
from discord import app_commands

# Custom modules
from components.api import API
from components.scheduler import Scheduler
from components.versionhandler import VersionHandler
from utils import id_generator, setup_logging, Color, translate_bldg

from apscheduler.job import Job

from apscheduler.jobstores.base import ConflictingIdError

building_regex = r"(([a-zA-Z0-9_]+-gen[2,3]_)|(([a-zA-Z0-9]+-){0,1}" \
    r"([a-zA-Z0-9]+_){1,3})|([a-zA-Z0-9_]+-22_))[C,U,R,E,L,M,S](10|[1-9])"

config = configparser.ConfigParser()

# copy default config to mounted docker volume
if not os.path.isfile("/app/data/config.cfg"):
    os.system("cp /root/Opportunity/default_config.cfg /app/data/config.cfg")

config_path = "/app/data/config.cfg"
config.read(config_path)

root_path = up(up(__file__))

env = os.environ.get
LOG_LEVEL = env("OPP_LOG_LEVEL", logging.INFO)
APS_LOG_LEVEL = env("OPP_APS_LOG_LEVEL", logging.INFO)
REQ_CACHE_LOG_LEVEL = env("OPP_REQ_CACHE_LOG_LEVEL", logging.INFO)
CACHE_TIMEOUT = env("OPP_CACHE_TIMEOUT", 300)
GIT_LOG_LEVEL = env("OPP_GIT_LOG_LEVEL", logging.INFO)
DISCORD_LOG_LEVEL = env("OPP_DISCORD_LOG_LEVEL", logging.INFO)
JSON_FOLDER = env("OPP_JSON_FOLDER", "/app/data/json")

class Bot(commands.Bot):

    def __init__(self):


        intents = discord.Intents.all()

        super().__init__(command_prefix=commands.when_mentioned_or('!'),
                         intents=intents)

        self.logger = logging.getLogger("opportunity.bot")
        log_path = os.path.join(root_path, "logs", "opportunity.log")
        setup_logging(
            "opportunity",
            level=LOG_LEVEL,
            root=True,
            log_path=log_path)
        logging.getLogger("discord").propagate = False
        logging.getLogger("discord").setLevel(DISCORD_LOG_LEVEL)
        logging.getLogger("apscheduler").setLevel(APS_LOG_LEVEL)
        logging.getLogger("requests_cache").setLevel(REQ_CACHE_LOG_LEVEL)
        logging.getLogger("git").setLevel(GIT_LOG_LEVEL)

        self.config = config

        self.data = load_data(self)

        self.scheduler: Scheduler = Scheduler(
            self.config['mariadb']['credentials'],
            self.config['mariadb']['database'])

        self.api: API = API(self)
        self.data["clean_bldg"] = self.api.get_building_names_clean()

        self.vh = VersionHandler()

        if not self.vh.is_latest:
            self.logger.warning(
                f"A new version is available: \x1b[31m" +
                f"{self.vh.local_version} \x1b[0m->" +
                f"\x1b[32m {self.vh.remote_version}")

        requests_cache.install_cache(
            "opportunity",
            backend="sqlite",
            expire_after=int(CACHE_TIMEOUT))

    async def on_ready(self):

        if self.user:
            self.logger.info(f'Logged in as {self.user} (ID: {self.user.id})')

        await self.change_presence(activity=discord.Game(
                                   name="Million on Mars"))

        self.scheduler.start()

        await load_cogs(self)
        await load_commands(self)
        self.emoji = await load_emojis(self)
        print(await self.tree.sync())

        # Reload help after self.extensions is populated
        await self.reload_extension("commands.help")


''' Variables '''

data: Dict[str, str] = {}

''' Functions '''

async def load_cogs(bot: Bot) -> None:
    components_path = os.path.join(root_path,
                                   "opportunity", "components", "cogs")
    for component in os.listdir(components_path):
        if os.path.isfile(os.path.join(components_path, component)):
            try:
                await bot.load_extension("components.cogs." + component[:-3])
                bot.logger.info(f"Successfully loaded '{component[:-3]}'")
            except Exception as e:
                bot.logger.error(e)

async def load_commands(bot: Bot) -> None:
    commands_path = os.path.join(root_path, "opportunity", "commands")
    for command in os.listdir(commands_path):
        if os.path.isfile(os.path.join(commands_path, command)):
            try:
                await bot.load_extension("commands." + command[:-3])
                bot.logger.info(f"Successfully loaded command '{command[:-3]}")
            except Exception as e:
                bot.logger.error(e)

def load_data(bot: Bot) -> Dict[str, Any]:
    data = {}
    for file in os.listdir(JSON_FOLDER):
        data[file[:-5]] = read_json(bot, os.path.join(JSON_FOLDER, file))
    return data

async def load_emojis(bot: Bot) -> Dict[str, str]:
    guild: discord.Guild = await bot.fetch_guild(1047714035661021245)
    emojis = await guild.fetch_emojis()
    result = {}
    for emoji in emojis:
        result[emoji.name] = str(emoji)
    return result

def read_json(bot: Bot, file: str) -> Optional[Dict[str, Any]]:
    ''' Read JSON config file and return it '''
    if os.path.isfile(file):
        if os.path.getsize(file) > 0:
            with open(file, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    filename = os.path.basename(file)
                    bot.logger.info(f"Successfully read file '{filename}'")
                except JSONDecodeError:
                    data = {}
                    bot.logger.error(f"Error reading file {file}.")
        else:
            bot.logger.info(f"Successfully read empty file {file}.")
            return {}
        return data
    return None

def save_json(bot: Bot, file: str, data: Dict[str, Any]) -> None:
    ''' Save data variable as JSON config file '''
    if os.path.isfile(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, indent=4)
        bot.logger.info(f"Successfully saved file {file}.")

async def print_general_usage(ctx: commands.Context) -> None:
    await ctx.send(f'''General usage: Not Implemented''')  # TODO Implement

async def print_error(ctx: commands.Context, error: Any) -> None:
    await ctx.send(content=f"Error: {error}")

async def edit_msg_to_error(msg: discord.Message, error: Any) -> None:
    await msg.edit(content=error)

async def remind(user: int, channel_id: int, **kwargs):
    channel = bot.get_channel(channel_id)
    u: discord.User = await bot.fetch_user(user)
    desc = f"Your **{kwargs['task_name']}** tasks are ready"
    em_msg = discord.Embed(title="Reminders", description=desc,
                           color=Color.GREEN)
    if isinstance(channel, discord.TextChannel):
        await channel.send(content=f"{u.mention}", embed=em_msg)


bot = Bot()

@bot.command(name="test")
async def test(ctx: commands.Context):
    bot.scheduler.add_job(
        remind,
        "date",
        next_run_time=dt.datetime.now()+dt.timedelta(seconds=10),
        kwargs={
            "user": ctx.author.id,
            "channel_id": ctx.channel.id,
            "task_name": "test"})
    print(bot.scheduler.running)
    print(str(bot.scheduler.get_jobs()))

async def building_ac(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    building: str = interaction.namespace.building
    choices = []
    if bot.data["clean_bldg"]:
        choices = bot.data["clean_bldg"]
        if len(building) >= 1:
            choices = [s for s in choices if building.lower() in s.lower()]
        if len(choices) > 25:
            choices = []
    return [
        app_commands.Choice(
            name=string.capwords(building.replace("_", " ")),
            value=building)
        for building in choices if current.lower() in building.lower()
    ]

async def recipe_ac(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    building: str = interaction.namespace.building
    level: int = interaction.namespace.level
    category = ""
    building = translate_bldg(building)
    if not category:
        category = building + "_C" + str(level)
    con = connect("opportunity.sqlite")
    con.row_factory = Row
    cur = con.cursor()
    cur.execute("SELECT * FROM prep WHERE category=?", (category,))
    recipes = json.loads(dict(cur.fetchone())["recipes"])
    choices = recipes
    r: Optional[str] = interaction.namespace.recipe
    if len(recipes) > 25:
        if r:
            choices = [s for s in choices if r.lower() in s.lower()]
        else:
            choices = []
        if len(choices) > 25:
            choices = []
    return [
        app_commands.Choice(name=recipes[recipe]["name"], value=recipe)
        for recipe in choices if current.lower() in recipe.lower()
    ]

@app_commands.command()
@app_commands.autocomplete(
    building=building_ac,
    recipe=recipe_ac)
async def reminder(
    interaction: discord.Interaction,
    building: str,
    level: app_commands.Range[int, 1, 10],
    recipe: str
) -> None:
    await interaction.response.defer(thinking=True)
    con = connect("opportunity.sqlite")
    con.row_factory = Row  # set query return type to dict
    cur = con.cursor()
    cur.execute("SELECT name, durationSeconds, inputs FROM recipes WHERE id=?",
                (recipe,))
    r = dict(cur.fetchone())
    try:
        job_id = id_generator(8)
        for _ in range(0, 100):
            try:
                bot.scheduler.add_job(
                    remind,
                    id=job_id,
                    trigger="date",
                    next_run_time=dt.datetime.now()+dt.timedelta(
                        seconds=int(r["durationSeconds"])),
                    kwargs={
                        "user": interaction.user.id,
                        "channel_id": interaction.channel_id,
                        "task_name": r["name"]})
            except ConflictingIdError as e:
                bot.logger.error("Conflicting id in job")
            except OperationalError as e2:
                bot.logger.error(e2)
                await interaction.followup.send(embed=discord.Embed(
                    title="Error",
                    description="Could not connect to database, " +
                                "please try again in a few seconds",
                    color=Color.RED
                ))
                return
            break
        task_time = r["durationSeconds"]
        m, s = divmod(int(task_time), 60)
        h, m = divmod(m, 60)
        task_time = '{:0>2}:{:0>2}:{:0>2}'.format(h, m, s)
        message = f"You will be reminded in **{task_time}** to " + \
                  f"finish your " + \
                  f"**{r['name']}** task(s)"
        em_msg = discord.Embed(
            title="Reminders",
            color=Color.GREEN,
            description=message)
        await interaction.followup.send(embed=em_msg)
    except Exception as e:
        bot.logger.error(e)

bot.tree.add_command(reminder)
if not config.has_option("discord", "TOKEN"):
    bot.logger.fatal("No discord bot token found")
    raise RuntimeError("No discord bot token found")
bot.run(config["discord"]["TOKEN"])
