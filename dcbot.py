import json
from json.decoder import JSONDecodeError
import os
import re

import datetime as dt
import logging
import configparser

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    Set,
    Sequence,
    Union
)

# Discord imports
import discord
from discord.ext import commands

# Custom modules
from views import Listings
from api import API
from notifications import Notifications
from scheduler import Scheduler
from utils import setup_logging, setup_logging_custom, id_generator

if TYPE_CHECKING:
    from apscheduler.job import Job

from apscheduler.jobstores.base import ConflictingIdError, JobLookupError

building_regex = r"(([a-zA-Z0-9_]+-gen[2,3]_)|(([a-zA-Z0-9]+-){0,1}" \
    r"([a-zA-Z0-9]+_){1,3})|([a-zA-Z0-9_]+-22_))[C,U,R,E,L,M,S](10|[1-9])"

config = configparser.ConfigParser()
config.read("config.cfg")

class Bot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned_or('!'),
                         intents=intents)

    async def on_ready(self):
        self.logger = logging.getLogger(__name__)
        stdout_hdlr, file_hdlr, warn = setup_logging(
            {"log_file": "bot.log", "log_level": logging.INFO})
        self.logger.addHandler(stdout_hdlr)
        self.logger.addHandler(file_hdlr)

        self.logger.info(f'Logged in as {self.user} (ID: {self.user.id})')

        await self.change_presence(activity=discord.Game(
                                   name="Million on Mars"))
        await self.add_cog(Notifications(bot))

        self.scheduler: Scheduler = Scheduler(config['mariadb']['credentials'])
        self.api: API = API(config["yourls"]["secret"])

        aps_logger = logging.getLogger("apscheduler")
        stdout_hdlr_aps, _ = setup_logging_custom(
            {"name": "apscheduler", "log_level": logging.INFO})
        aps_logger.addHandler(stdout_hdlr_aps)

        self.scheduler.start()

        self.recipes = read_json("test/recipes.json")


''' Variables '''

data: Dict[str, str] = {}

''' Functions '''

def read_json(file: str) -> Dict[str, Any]:
    ''' Read JSON config file and return it '''
    if os.path.isfile(file):
        if os.path.getsize(file) > 0:
            with open(file, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    bot.logger.info(f"Successfully read file {file}.")
                except JSONDecodeError:
                    data = {}
                    bot.logger.info(f"Error reading file {file}.")
        else:
            bot.logger.info(f"Successfully read empty file {file}.")
            return {}
        return data

def save_json(file: str, data: Dict[str, Any]) -> None:
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

bot = Bot()

@bot.command(name="ah", aliases=["search"])
async def ah(ctx: commands.Context, name: str, rarity: str = None) -> None:
    '''
    Search AtomicHub marketplace

    Parameters:
        building (str): name of building

    Returns:
        Nothing, message is sent in channel
    '''
    got_listings = False
    amount = 1
    error = f"{name} did not match the required scheme."
    try:
        message = await ctx.send(ctx.author.mention + "\nGetting listings...")
        regex = re.compile("".join([building_regex, r"=[0-9]{0,1}"]))
        if re.match(regex, name):
            amount = int(name[-1])
            name = name[:-2]
            listings = bot.api.get_listings(name, 1, amount)
            got_listings = True
        elif re.match(building_regex, name):
            pass
        elif rarity is not None:
            name = f"{name}_{rarity.upper()}"
            if re.match(r'(([a-zA-Z0-9_]+-gen[2,3]_)|(([a-zA-Z0-9]+-){0,1}' +
                        '([a-zA-Z0-9]+_){1,3})|([a-zA-Z0-9_]+-22_))' +
                        '[C,U,R,E,L,M,S](10|[1-9])', name):
                pass
            else:
                await edit_msg_to_error(message, error)
                return
        else:
            await edit_msg_to_error(message, error)
            return
        if not got_listings:
            listings: Optional[Dict[Union[str, int], Any]] = \
                bot.api.get_listings(name, 1, 1)
        if listings:
            description = f"Listings containing {amount} {name} (page 1)"
            em_msg = discord.Embed(
                title="Listings",
                description=description,
                color=0x00ff00)

            em_msg.add_field(
                name="Listings",
                value="\n".join([
                    listings[item]["link"] for item in listings.keys()]),
                inline=True)

            em_msg.add_field(
                name="Cost",
                value="\n".join([
                    str(listings[item]["price"]) +
                    " " + listings[item]["token_symbol"]
                    for item in listings.keys()]),
                inline=True)

            em_msg.add_field(
                name="Land(s)",
                value="\n".join([
                    listings[item]["land"]["rarity"]
                    if isinstance(listings[item]["land"], dict)
                    else "Bundle" for item in listings.keys()]),
                inline=True)
            # em_msg.add_field(
            # name="Building(s)",
            # value="\n".join([str(listings[item]["land"]["buildings"].keys())
            # for item in listings.keys()]), inline=True)
            await message.delete()
            view = Listings(name, 1)
            msg = await ctx.send(ctx.author.mention, embed=em_msg, view=view)
        else:
            error = f"Could not find any listings matching {name} with " + \
                    f"amount {amount}.\nMaybe try using a building from " + \
                    f"!buildings with rarity from !level ."
            await ctx.send(error)
    except Exception as e:
        print(e)
        await ctx.send(f"Could not search AH.\n{e}")

@ah.error
async def ah_error(ctx: commands.Context, err: commands.CommandError) -> None:
    if isinstance(err, commands.MissingRequiredArgument):
        await print_general_usage(ctx, err)
    elif isinstance(err, commands.CheckFailure):
        await ctx.send('You dont have the permission to use that command')

@bot.command(name="buildings", aliases=["bldg", "building"])
async def buildings(ctx: commands.Context):
    '''
    List all buildings

    Returns:
        Nothing, message is sent in channel
    '''
    try:
        msg = await ctx.send(ctx.author.mention + """\nGetting buildings..""")
        buildings = bot.api.get_buildings()
        buildings = bot.api.extract_data(buildings, "name")
        factories = []
        artifacts = []
        for item in buildings:
            if item not in ["total_space", "available_space"]:
                name = item.rsplit("_", 1)
                if len(name) > 1:
                    if name[1] == "A":
                        if not name[0] in artifacts:
                            artifacts.append(name[0])
                    else:
                        if not name[0] in factories:
                            factories.append(name[0])
        description = "List of all buildings"
        em_msg = discord.Embed(title="Buildings", description=description,
                               color=0x00ff00)
        em_msg.add_field(name="Factories", value="\n".join(factories),
                         inline=True)
        em_msg.add_field(name="Artifacts", value="\n".join(artifacts),
                         inline=True)
        await msg.delete()
        msg = await ctx.send(ctx.author.mention, embed=em_msg)
    except Exception as e:
        print(e)
        await ctx.send(f"Error listing all buildings.\n{e}")

@buildings.error
async def buildings_error(ctx: commands.Context, err: commands.CommandError):
    if isinstance(err, commands.CheckFailure):
        await ctx.send('You dont have the permission to use that command')

@bot.command(name="level", aliases=["levels", "lvl"])
async def level(ctx: commands.Context):
    '''
    List all building level

    Returns:
        Nothing, message is sent in channel
    '''
    try:
        message = await ctx.send(ctx.author.mention + """\nGetting level..""")

        mythic = "C-M"
        special = "S"
        other_lvl = "10 (C), 8 (U), 6 (R), 5 (E-M)"
        buildings = {
            "solar_panel": {"rarities": mythic, "max_level": "10"},
            "cad": {"rarities": mythic, "max_level": "10"},
            "greenhouse": {"rarities": mythic, "max_level": "10"},
            "water_filter": {"rarities": mythic, "max_level": "10"},
            "grindnbrew": {"rarities": mythic, "max_level": "10"},
            "polar_workshop": {"rarities": mythic, "max_level": "10"},
            "mining_rig": {"rarities": mythic, "max_level": "10"},
            "smelter": {"rarities": mythic, "max_level": "10"},
            "machine_shop": {"rarities": mythic, "max_level": "10"},
            "sab_reactor": {"rarities": mythic, "max_level": "10"},
            "chem_lab": {"rarities": mythic, "max_level": "10"},
            "3d_print_shop": {"rarities": mythic, "max_level": "10"},
            "rover_works": {"rarities": mythic, "max_level": other_lvl},
            "cantina": {"rarities": special, "max_level": "6"},
            "bazaar": {"rarities": special, "max_level": "5"},
            "teashop": {"rarities": special, "max_level": "5"},
            "pirate_radio": {"rarities": special, "max_level": "10"},
            "library": {"rarities": special, "max_level": "10"},
            "training_hall": {"rarities": special, "max_level": "10"},
            "engineering_bay": {"rarities": mythic, "max_level": other_lvl},
            "concrete_habitat": {"rarities": mythic, "max_level": "5"},
            "shelter": {"rarities": mythic, "max_level": "5"},
            "gallery": {"rarities": special, "max_level": "10"},
            "metis_shield": {"rarities": mythic, "max_level": "1"},
            "thorium_reactor": {"rarities": mythic, "max_level": other_lvl}}
        rarities = ["C-M"]
        em_msg = discord.Embed(title="Buildings",
                               description="List of all buildings\n" +
                               "Possible rarities: C, U, R, E, L, M",
                               color=0x00ff00)
        em_msg.add_field(name="Buildings",
                         value="\n".join(buildings.keys()), inline=True)
        em_msg.add_field(name="Rarities",
                         value="\n".join([
                              buildings[x]["rarities"] for x in buildings]),
                         inline=True)
        em_msg.add_field(name="Level",
                         value="\n".join([buildings[x]["max_level"]
                                          for x in buildings]),
                         inline=True)
        await message.delete()
        message = await ctx.send(ctx.author.mention, embed=em_msg)
    except Exception as e:
        print(e)
        await ctx.send(f"Error listing all level.\n {e}")


@bot.command(name="test")
async def test(ctx: commands.Context):
    bot.scheduler.add_job(
        remind,
        "date",
        next_run_time=dt.datetime.now()+dt.timedelta(seconds=10),
        kwargs={"user": ctx.author.id, "channel_id": ctx.channel.id})

async def remind(user: int, channel_id: int, **kwargs):
    channel = bot.get_channel(channel_id)
    u: discord.User = await bot.fetch_user(user)
    em_msg = discord.Embed(title="tasks ready")
    await channel.send(content=f"{u.mention}", embed=em_msg)

@bot.command(name="start", aliases=["addreminder"])
async def start(ctx: commands.Context, task: str) -> None:
    try:
        task_dir = bot.recipes[task]
        print(task_dir)
    except KeyError as e:
        bot.logger.error(f"{task} key not found in recipes.")
        await ctx.send(f"{task} not found in recipes.")
        return
    job_id = id_generator(8)
    for _ in range(0, 100):
        try:
            bot.scheduler.add_job(
                remind,
                id=job_id,
                trigger="date",
                next_run_time=dt.datetime.now()+dt.timedelta(
                    seconds=task_dir["durationSeconds"]),
                kwargs={
                    "user": ctx.author.id,
                    "channel_id": ctx.channel.id,
                    "task_name": task_dir["name"]})
        except ConflictingIdError as e:
            bot.logger.error("Conflicting id in job")
        break
    task_time = task_dir["durationSeconds"]
    m, s = divmod(task_time, 60)
    h, m = divmod(m, 60)
    task_time = '{:0>2}:{:0>2}:{:0>2}'.format(h, m, s)
    message = f"I'll remind you in {task_time} to " + \
              f"finish your {task_dir['name']} task(s)"
    em_msg = discord.Embed(color=0x424949)
    em_msg.add_field(name="Recipes", value=message)
    await ctx.send(embed=em_msg, ephemeral=True)

@bot.command(name="delreminder", aliases=["stop"])
async def delreminder(ctx: commands.Context, job_id: str) -> None:
    try:
        bot.scheduler.remove_job(job_id)
        await ctx.send(f"Successfully removed job with id {job_id}")
    except JobLookupError as e:
        bot.logger.error(f"Could not find job with id {job_id}")
        await ctx.send(f"Could not find reminder with id {job_id}")

@bot.command(name="reminders", aliases=["reminder"])
async def reminders(ctx: commands.Context) -> None:
    jobs: Sequence[Job] = bot.scheduler.get_user_jobs(ctx.author.id)
    message = f"{jobs}"
    em_msg = discord.Embed(title=f"Reminders for {ctx.author.display_name}",
                           color=0x424949)

    task_names = "\n".join([job.kwargs["task_name"] for job in jobs])
    em_msg.add_field(name=f"Task", value=task_names)

    remind_time = "\n".join([
        str(job.next_run_time.replace(microsecond=0)) for job in jobs])
    em_msg.add_field(name="Due time", value=remind_time)

    ids = "\n".join([job.id for job in jobs])
    em_msg.add_field(name="ID", value=ids)

    await ctx.send(embed=em_msg)

@bot.command(name="tasks")
async def tasks(ctx: commands.Context) -> None:
    msg = f"A complete list of all recipes is available at " + \
          f"http://mom.keedosuul.de/recipes"
    em_msg = discord.Embed(color=0x424949)
    em_msg.add_field(name="Recipes", value=msg)
    await ctx.send(embed=em_msg)

bot.run(config["discord"]["TOKEN"])
