import time
import discord
import json
import os
import typing
from discord.ext import commands
from base.modules.access_checks import has_mod_role
from datetime import datetime, timezone
from base.modules.serializable_object import MessageCache, MessageSchedule, CommandSchedule
from base.modules.basic_converter import FutureTimeConverter, PastTimeConverter, EmojiUnion
from base.modules.constants import CACHE_PATH as path
from base.modules.message_helper import get_message_attachments, send_temp_message, wait_user_confirmation,\
                                        save_message, get_message_brief, get_full_message, clean_message_files
from base.modules.special_bot_methods import special_process_command, command_check

class MessageManagementCog(commands.Cog, name="Message Management Commands"):
  def __init__(self, bot):
    self.bot = bot
    if not os.path.isdir(path):
      os.mkdir(path)
    self.delete_cache = MessageCache.from_json(f'{path}/delete_cache.json')
    self.scheduler = MessageSchedule.from_json(f'{path}/scheduler.json')
    for guild in self.bot.guilds:
      self.init_guild(guild)
    
  def init_guild(self, guild):
    if guild.id not in self.delete_cache:
      self.delete_cache[guild.id] = []
    if guild.id not in self.scheduler:
      self.scheduler[guild.id] = []
    # set up the timers for these schedulers
    for schedule in self.scheduler[guild.id]:
      schedule.set_timer(guild, self.bot, self.scheduler[guild.id])
  
  def cog_unload(self):
    try:
      with open(f'{path}/delete_cache.json', 'w') as f:
        json.dump(self.delete_cache, f)
    except:
      pass
    for key, msglist in self.scheduler.items():
      for msg in msglist:
        msg.cancel()
    try:
      with open(f'{path}/scheduler.json', 'w') as f:
        json.dump(self.scheduler, f)
    except:
      pass
      
  def get_max_cache(self, guild):
    return self.bot.get_setting(guild, "NUM_DELETE_CACHE")

  async def cog_command_error(self, context, error):
    if hasattr(context.command, "on_error"):
      # This prevents any commands with local handlers being handled here.
      return
    if isinstance(error, commands.BotMissingPermissions):
      await context.send(f"Sorry {context.author.mention}, but I do not have permission to manage messages!")
    elif isinstance(error, commands.CheckFailure):
      await context.send(f"Sorry {context.author.mention}, but you do not have permission to manage messages!")
    elif isinstance(error, commands.UserInputError):
      await context.send(f"Sorry {context.author.mention}, but I could not understand the arguments passed to `?{context.command.qualified_name}`.")
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
      await context.send(f"Sorry {context.author.mention}, but I do not have permission to post in the specified channel.")
    else:
      await context.send(f"Sorry {context.author.mention}, but something unexpected happened...")

  @commands.group(
    name="delete",
    brief="Deletes messages",
    help="Deletes n messages from member(s) in a channel with skipping m messages, if member is not specified it will delete any message, if channel is not specified it will search the current channel.",
    aliases=["del"],
    usage="[@mentions]... [#channel] [number=1] [skip_num=0]",
    case_insensitive = True,
    invoke_without_command=True
  )
  @commands.bot_has_permissions(read_messages=True, read_message_history=True, manage_messages=True)
  @has_mod_role()
  async def _delete(self, context, members:commands.Greedy[discord.Member], channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1, skip_num:typing.Optional[int]=0):
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    if channel is None:
      channel = context.channel
    max_cache = self.get_max_cache(context.guild)
    msg_list = []
    msg_count = 0
    async for message in channel.history():
      if len(msg_list) >= num:
        break
      if message.id == context.message.id:
        continue # skip the command
      if len(members) == 0 or message.author in members:
        msg_count += 1
        if msg_count > skip_num: # skip the first m messages
          await self.cache_message(message, max_cache)
          msg_list.append(message)
    await self.smart_delete_messages(channel, msg_list)
    title = f"Messages have been deleted"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Author(s)":"\n".join([f"{member.mention} {member}" for member in members]) if members else None,
              "Channel":channel.mention,
              "Deleted":f"{len(msg_list)} message(s)"}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    if len(msg_list) == 0:
      await send_temp_message(context, "Could not delete message(s).", 10)
    await context.message.delete()
    
  async def smart_delete_messages(self, channel, msg_list):
    # this will use bulk delete if possible
    minimum_time = int((time.time() - 14 * 24 * 60 * 60) * 1000.0 - 1420070400000) << 22
    bulk_msg = []
    for msg in msg_list:
      if msg.id < minimum_time: # older than 14 days
        await msg.delete()
      else:
        bulk_msg.append(msg)
    await channel.delete_messages(bulk_msg)
    
  async def cache_message(self, message, max_cache):
    key = message.channel.guild.id
    if max_cache > 0 and (len(self.delete_cache[key]) < max_cache or (len(self.delete_cache[key]) > 0 and 
      time.mktime(message.created_at.timetuple()) > self.delete_cache[key][-1]["time"])):
      # do not need to cache the message if it's too old
      self.delete_cache[key].append(await MessageCache.from_message(message))
      self.delete_cache[key].sort(reverse=True) # the latest message should be at front
      if (len(self.delete_cache[key]) > max_cache):
        # remove the oldest message if it exceeds the max cache
        self.delete_cache[key].pop().del_files()
    
        
  @_delete.command(
    name="restore",
    brief="Restores deleted messages",
    usage="[@mentions]... [#channels]... [number=1] [skip_num=0]",
    help="A command to restore n deleted messages from user(s) in channel(s) with skipping m messages. It restores message(s) to the current channel. If member/channel is not specified it will search through all messages in cache.",
  )
  @commands.bot_has_permissions(read_messages=True, send_messages=True, manage_messages=True)
  @has_mod_role()
  async def _restore(self, context, members:commands.Greedy[discord.Member], channels:commands.Greedy[discord.TextChannel], num:typing.Optional[int]=1, skip_num:typing.Optional[int]=0):
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    if len(self.delete_cache[context.guild.id]) == 0:
      await send_temp_message(context, "Sorry, but no deleted message is found.", 10)
      await context.message.delete()
      return
    queue = []
    msg_count = 0
    members_id = [member.id for member in members]
    channels_id = [channel.id for channel in channels]
    for message_cache in self.delete_cache[context.guild.id]:
      if len(queue) >= num:
        break
      if (len(members) == 0 or message_cache["author"] in members_id) and (len(channels) == 0 or message_cache["channel"] in channels_id):
        msg_count += 1
        if msg_count > skip_num:
          queue.append(message_cache)
    restored = 0
    while len(queue) > 0:
      try:
        await self.restore_message(queue.pop(), context)
        restored += 1
      except:
        pass
    title = f"Messages have been restored"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Author(s)":"\n".join([f"{member.mention} {member}" for member in members]) if members else None,
              "From Channel(s)":"\n".join([channel.mention for channel in channels]) if channels else None,
              "To Channel":context.channel.mention,
              "Restored":f"{restored} message(s)"}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    if restored == 0:
      await send_temp_message(context, "Could not restore message(s).", 10)
    await context.message.delete()
    
  async def restore_message(self, message_cache, context):
    await message_cache.restore(context)
    self.delete_cache[context.guild.id].remove(message_cache)
    
  @_delete.command(
    name="cache",
    brief="Shows cache of deletes",
    help="A command to show the information of latest deleted messages.",
    aliases=["list"]
  )
  @commands.bot_has_permissions(read_messages=True, send_messages=True)
  @has_mod_role()
  async def _cache(self, context):
    if len(self.delete_cache[context.guild.id]) == 0:
      await send_temp_message(context, "Sorry, but no deleted message is found.", 10)
      await context.message.delete()
      return
    embed = discord.Embed(title=f"Cache of Deletes", colour=discord.Colour.green(), timestamp=context.message.created_at)
    i = 1
    for message_cache in self.delete_cache[context.guild.id]:
      channel = context.guild.get_channel(message_cache['channel'])
      member = self.bot.get_user(message_cache['author'])
      msg = (
        f"Channel: {channel.mention if channel is not None else 'Unknown Channel'}\n"
        f"Author: {member.mention if member is not None else 'Unknown Member'}\n"
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(message_cache['time']))} UTC\n"
      )
      if message_cache['content']:
        msg += f"Content length: {len(message_cache['content'])}\n"
      if message_cache['embed'] is not None:
        msg += f"Embed size: {len(discord.Embed.from_dict(message_cache['embed']))}\n"
      if len(message_cache['files']) > 0:
        msg += f"Files: {len(message_cache['files'])} file(s)\n"
      embed.add_field(name=f"Cache {i}:", value=msg)
      i += 1
    embed.set_footer(text="DELETE CACHE")
    await context.send(content=None, embed=embed)

  @commands.command(
    name="announce",
    brief="Makes an announcement",
    usage="[#channel] <announcement>",
    help="This command makes an announcement in the specified channel to all users, if channel is not specified it will post at the current channel.",
    aliases=["post", "announcement"]
  )
  @commands.has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @has_mod_role()
  async def _announce(self, context, channel:typing.Optional[discord.TextChannel], *, announcement=None):
    if channel is None:
      channel = context.channel
    (embed_post, files) = await get_message_attachments(context.message)
    if announcement is None and embed_post is None and len(files) == 0:
      await context.send_help("announce")
      return
    await channel.send(content=announcement, embed=embed_post, files=files)
    title = f"An announcement has been made"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":channel.mention,
              "Content":(announcement[:1021] + '...') if announcement and len(announcement) > 1021 else announcement,
              "Embed size":len(embed_post) if embed_post else None,
              "Files":f"{len(files)} file(s)" if files else None}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    await context.message.delete()

  @commands.command(
    name="move",
    brief="Moves messages",
    help="This command moves n messages (from the past 100 messages of the channel) from user(s) with skipping m messages into a channel. It will move the message from the @mention to #channel. If no number is specified, it will move the last message. If no member is specified, it will search any messages.",
    usage="[@mentions]... <#channel> [number=1] [skip_num=0]",
  )
  @commands.bot_has_permissions(read_messages=True, send_messages=True, read_message_history=True, manage_messages=True)
  @has_mod_role()
  async def _move_post(self, context, members:commands.Greedy[discord.Member], channel:discord.TextChannel, num:typing.Optional[int]=1, skip_num:typing.Optional[int]=0):
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    queue = []
    msg_count = 0
    async for message in context.message.channel.history():
      if len(queue) >= num:
        break
      if context.message.id == message.id: # skip the command message
        continue
      if len(members) == 0 or message.author in members:
        msg_count += 1
        if msg_count > skip_num:
          queue.append(message)
    for msg in reversed(queue):
      await self.copy_message(channel, msg)
    await self.smart_delete_messages(context.message.channel, queue)
    title = f"Messages have been moved"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Author(s)":"\n".join([f"{member.mention} {member}" for member in members]) if members else None,
              "From Channel":context.channel.mention,
              "To Channel":channel.mention,
              "Moved":f"{len(queue)} message(s)"}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    if len(queue) == 0:
      await send_temp_message(context, "Could not move message(s).", 10)
    await context.message.delete()
    
  async def copy_message(self, channel, message):
    old_message = message.content
    content = old_message.replace('\n> ', '\n').replace('\n', '\n> ')
    if content:
      content = f"{message.author.mention} said in {message.channel.mention}:\n> {content}"
    else:
      content = f"{message.author.mention} said in {message.channel.mention}:"
    (embedOrigin, files) = await get_message_attachments(message)
    await channel.send(
      content=content,
      embed=embedOrigin,
      files = files
    )
    
  @commands.group(
    name="edit",
    brief="Edits a message",
    usage="[#channel] [number=1] <text>",
    help="Edits the n-th last message of the bot, if channel is not specified it will edit the message at the current channel.",
    case_insensitive = True,
    invoke_without_command=True
  )
  @has_mod_role()
  async def _edit(self, context, channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1, *, text=None):
    if text is None:
      await context.send_help("edit")
      return
    await self.general_edit(context, channel, text, lambda x,y: y, num)
  
  @_edit.command(
    name="add",
    brief="Edits by adding a line",
    usage="[#channel] [number=1] <text>"
  )
  @commands.bot_has_permissions(read_messages=True, send_messages=True, read_message_history=True, manage_messages=True)
  @has_mod_role()
  async def _edit_add(self, context, channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1, *, text=None):
    if text is None:
      await context.send_help("edit add")
      return
    await self.general_edit(context, channel, text, lambda x,y: x + "\n" + y, num)
    
  @_edit.command(
    name="replace",
    brief="Edits by replacing the last line",
    usage="[#channel] [number=1] <text>",
  )
  @commands.bot_has_permissions(read_messages=True, send_messages=True, read_message_history=True, manage_messages=True)
  @has_mod_role()
  async def _edit_replace(self, context, channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1, *, text=None):
    if text is None:
      await context.send_help("edit replace")
      return
    await self.general_edit(context, channel, text, lambda x,y: x.rsplit(sep="\n", maxsplit=1)[0] + "\n" + y, num)
    
  @_edit.command(
    name="remove",
    brief="Edits by removing the last line",
    usage="[#channel] [number=1]",
    aliases=["rm"]
  )
  @commands.bot_has_permissions(read_messages=True, send_messages=True, read_message_history=True, manage_messages=True)
  @has_mod_role()
  async def _edit_remove(self, context, channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1):
    await self.general_edit(context, channel, None, lambda x,y: x.rsplit(sep="\n", maxsplit=1)[0], num)
    
  async def general_edit(self, context, channel, text, edit_method, num=1):
    # a general function for editing message, edit_method is a function that takes old content and new contend to build the new message
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    if channel is None:
      channel = context.channel
    msg_count = 0
    async for message in channel.history():
      if message.author.id == self.bot.user.id:
        msg_count += 1
        if num == msg_count:
          await self.edit_message(context, message, text, edit_method)
          break
    else:
      title = f"Could not edit a message"
      description=f"No message number {num} found from bot"
      await self.bot.log_mod(context.guild, title=title, description=description, timestamp=context.message.created_at)
      await send_temp_message(context, "Could not edit a message.", 10)
    await context.message.delete()
    
  async def edit_message(self, context, message, text, edit_method):
    old_content = message.content
    new_content = edit_method(old_content, text)
    await message.edit(content=new_content)
    title = f"A bot message has been edited"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":message.channel.mention,
              "Old Content":'...' + (old_content[-1021:]) if len(old_content) > 1021 else old_content,
              "New Content:":'...' + (new_content[-1021:]) if len(new_content) > 1021 else new_content}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    
  @commands.command(
    name="react",
    brief="Adds a reaction",
    usage="[@mention] [#channel] [number=1] <emojis>...",
    help="This command makes a reaction to the n-th last message from a user in a channel, if member is not specified it will search the n-th last message, if channel is not specified it will search the current channel.",
  )
  @commands.has_permissions(read_messages=True, add_reactions=True)
  @commands.bot_has_permissions(read_messages=True, add_reactions=True)
  @has_mod_role()
  async def _react(self, context, member:typing.Optional[discord.Member], channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1, emojis:commands.Greedy[EmojiUnion]=None):
    if channel is None:
      channel = context.channel
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    if emojis == None or len(emojis) == 0:
      await context.send_help("react")
      return
    msg_count = 0
    async for message in channel.history():
      if context.message.id == message.id: # skip the command message
        continue
      if member is None or message.author.id == member.id:
        msg_count += 1
        if num == msg_count:
          await self.add_reaction(context, message, emojis)
          break
    else:
      title = f"Could not add reaction(s)"
      description=f"No message number {num} found{' from '+member.mention if member is not None else ''} in {channel.mention}"
      await self.bot.log_mod(context.guild, title=title, description=description, timestamp=context.message.created_at)
      await send_temp_message(context, "Could not add reaction(s).", 10)
    await context.message.delete()
    
  async def add_reaction(self, context, message, emojis):
    for emoji in emojis:
      await message.add_reaction(emoji)
    title = f"Reaction(s) have been added"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Author":f"{message.author.mention}\n{message.author}",
              "Channel":message.channel.mention,
              "Reaction(s)":''.join([str(emoji) for emoji in emojis])}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    
  @commands.group(
    name="schedule",
    brief="Schedules a message",
    help="Schedules to send a message in a specific channel. Time has to be in \"%d%h%m%s\" format or a formatted absolute time. If channel is not specified, it will send the message to the current channel. If no content is specified, it will send a reminder mentioning the author.\n\nFor example, to schedule a text in 2 hours 10 minites, use:\n?schedule 2h10m some text\nTo schedule a text at Sep 10 10am at timezone -05:00, use:\n?schedule \"9-10 10am -0500\" some text\nWithout a timezone the absolute time will be interpreted as UTC time.",
    usage="[#channel] <time> [text]",
    case_insensitive = True,
    invoke_without_command=True
  )
  @commands.has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @has_mod_role()
  async def _schedule(self, context, channel:typing.Optional[discord.TextChannel], schedule:FutureTimeConverter, *, text=None):
    if schedule <= datetime.now(timezone.utc):
      await context.send(f"The time your input: {schedule.strftime('%Y-%m-%d %H:%M:%S %z')} is not a future time.")
      return
    if channel is None:
      channel = context.channel
    message_schedule = await MessageSchedule.from_message(context.message, channel, schedule, text)
    self.scheduler[context.guild.id].append(message_schedule)
    message_schedule.set_timer(context.guild, self.bot, self.scheduler[context.guild.id])
    title = f"A message has been scheduled"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":channel.mention,
              "Content":(text[:1021] + '...') if text and len(text) > 1021 else text,
              "Embed size":len(discord.Embed.from_dict(message_schedule['embed'])) if message_schedule['embed'] else None,
              "Files":f"{len(message_schedule['files'])} file(s)" if message_schedule["files"] else None,
              "Time":schedule.strftime('%Y-%m-%d %H:%M:%S %z')}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    await send_temp_message(context, f"{context.author.mention} You have scheduled a message at {schedule.strftime('%Y-%m-%d %H:%M:%S %z')}.", 10)
    await context.message.delete()
    
  @_schedule.command(
    name="list",
    brief="Shows a list of scheduled messages",
    help="A command to show a list of scheduled messages.",
  )
  @commands.has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @has_mod_role()
  async def _list_schedule(self, context):
    if len(self.scheduler[context.guild.id]) == 0:
      await send_temp_message(context, "Sorry, but no scheduled message is found.", 10)
      await context.message.delete()
      return
    embed = discord.Embed(title=f"Scheduled Messages", colour=discord.Colour.green(), timestamp=context.message.created_at)
    i = 1
    for message_schedule in self.scheduler[context.guild.id]:
      channel = context.guild.get_channel(message_schedule['channel'])
      member = self.bot.get_user(message_schedule['author'])
      msg = (
        f"Author: {member.mention if member is not None else 'Unknown Member'}\n"
        f"To be Sent in: {channel.mention if channel is not None else 'Unknown Channel'}\n"
        f"Scheduled at: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(message_schedule['time']))} UTC\n"
      )
      if "cmd" in message_schedule and message_schedule["cmd"]:
        msg += f"Command: {message_schedule['content']}\n"
      elif message_schedule['content']:
        msg += f"Content length: {len(message_schedule['content'])}\n"
      if message_schedule['embed'] is not None:
        msg += f"Embed size: {len(discord.Embed.from_dict(message_schedule['embed']))}\n"
      if len(message_schedule['files']) > 0:
        msg += f"Files: {len(message_schedule['files'])} file(s)\n"
      embed.add_field(name=f"Message {i}:", value=msg)
      i += 1
    embed.set_footer(text="DELETE CACHE")
    await context.send(content=None, embed=embed)
    
  @_schedule.command(
    name="cancel",
    brief="Cancels a scheduled message",
    usage="[@mention] [#channel] [number=1]",
    help="A command to cancel the n-th last scheduled message from a member to be sent in a channel.",
  )
  @commands.has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @has_mod_role()
  async def _cancel_schedule(self, context, member:typing.Optional[discord.Member], channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1):
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    if len(self.scheduler[context.guild.id]) == 0:
      await send_temp_message(context, "Sorry, but no scheduled message is found.", 10)
      await context.message.delete()
      return
    msg_count = 0
    for message_schedule in self.scheduler[context.guild.id]:
      if (member is None or message_schedule["author"] == member.id) and (channel is None or message_schedule["channel"] == channels.id):
        msg_count += 1
        if msg_count == num:
          message_schedule.cancel()
          file_num = len(message_schedule['files'])
          message_schedule.delete_schedule(self.scheduler[context.guild.id])
          if member is None:
            member = context.guild.get_member(message_schedule["author"])
          if channel is None:
            channel = context.guild.get_channel(message_schedule['channel'])
          title = f"A scheduled message have been cancelled"
          fields = {"Cancelled by":f"{context.author.mention}\n{context.author}",
                    "Author":member.mention if member else 'Unknown Member',
                    "Channel":channel.mention if channel else 'Unknown Channel',
                    "Content":(message_schedule['content'][:1021] + '...') if message_schedule['content'] and len(message_schedule['content']) > 1021 else message_schedule['content'],
                    "Embed size":len(discord.Embed.from_dict(message_schedule['embed'])) if message_schedule['embed'] else None,
                    "Files":f"{file_num} file(s)" if file_num else None}
          await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
          await send_temp_message(context, "A scheduled message has been cancelled.", 10)
          break
    else:
      title = f"Could not cancel a scheduled message"
      description=f"No scheduled message number {num} found{' from '+member.mention if member is not None else ''}{' to be sent in '+channel.mention if channel is not None else ''}"
      await self.bot.log_mod(context.guild, title=title, description=description, timestamp=context.message.created_at)
      await send_temp_message(context, "Could not cancel a scheduled message.", 10)
    await context.message.delete()
    
  @_schedule.command(
    name="sendnow",
    brief="Sends a scheduled message right now",
    usage="[@mention] [#channel] [number=1]",
    aliases=["now", "send"],
    help="A command to immediately send the n-th last scheduled message from a member to be sent in a channel.",
  )
  @commands.has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @has_mod_role()
  async def _sendnow_schedule(self, context, member:typing.Optional[discord.Member], channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1):
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    if len(self.scheduler[context.guild.id]) == 0:
      await send_temp_message(context, "Sorry, but no scheduled message is found.", 10)
      await context.message.delete()
      return
    msg_count = 0
    for message_schedule in self.scheduler[context.guild.id]:
      if (member is None or message_schedule["author"] == member.id) and (channel is None or message_schedule["channel"] == channels.id):
        msg_count += 1
        if msg_count == num:
          message_schedule.cancel()
          await message_schedule.send_now(context.guild, self.bot, context.author)
          message_schedule.delete_schedule(self.scheduler[context.guild.id])
          break
    else:
      title = f"Could not send a scheduled message"
      description=f"No scheduled message number {num} found{' from '+member.mention if member is not None else ''}{' to be sent in '+channel.mention if channel is not None else ''}"
      await self.bot.log_mod(context.guild, title=title, description=description, timestamp=context.message.created_at)
      await send_temp_message(context, "Could not send a scheduled message.", 10)
    await context.message.delete()
    
  @_schedule.command(
    name="cmd",
    brief="Schedules a command",
    usage="<time> <cmd>",
    aliases=["command"],
    help="A command to schedule a command in the current channel. Your command text should have the same format as the ordinary command with arguments. This command checks the argument of your command by parsing it into a context but there is no guarantee that the command is error-free. If a command needs to read \"context.messgae.content\" then it may not work as expected. And notice that if your command message is deleted before the scheduled time the command may not work.",
  )
  @commands.has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, send_messages=True, mention_everyone=True, manage_messages=True)
  @has_mod_role()
  async def _cmd_schedule(self, context, schedule:FutureTimeConverter, *, cmd):
    if schedule <= datetime.now(timezone.utc):
      await context.send(f"The time your input: {schedule.strftime('%Y-%m-%d %H:%M:%S %z')} is not a future time.")
      return
    try:
      await command_check(self.bot, context.message, cmd)
    except Exception as e:
      await context.send(f"Sorry {context.author.mention}, but the scheduled command cannot run: {e}")
      return
    commandSchedule = await CommandSchedule.from_message(context.message, schedule, cmd)
    self.scheduler[context.guild.id].append(commandSchedule)
    commandSchedule.set_timer(context.guild, self.bot, self.scheduler[context.guild.id])
    title = f"A command has been scheduled"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":context.message.channel.mention,
              "Command":cmd,
              "Time":schedule.strftime('%Y-%m-%d %H:%M:%S %z')}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    await send_temp_message(context, f"{context.author.mention} You have scheduled a command at {schedule.strftime('%Y-%m-%d %H:%M:%S %z')}.", 10)
    
  @commands.command(
    name="remind",
    brief="Sends a reminder message",
    usage="<time> [text]",
    help="Schedules to send a reminder message mentioning the author in future in the current channel. Time has to be in \"%d%h%m%s\" format or a formatted absolute time. If no text is specified it will just send a default reminder. All the mentions in the text will be removed.\n\nFor example, to schedule a text in 2 hours 10 minites, use:\n?remind 2h10m some text\nTo schedule a text at Sep 10 10am at timezone -05:00, use:\n?remind \"9-10 10am -0500\" some text\nWithout a timezone the absolute time will be interpreted as UTC time.",
    aliases=["reminder"]
  )
  @commands.has_permissions(read_messages=True, send_messages=True)
  @commands.bot_has_permissions(read_messages=True, send_messages=True, manage_messages=True)
  async def _remind(self, context, schedule:FutureTimeConverter, *, text:commands.clean_content=None):
    if text is not None:
      text = f"{context.author.mention} {text}"
    await context.invoke(self.bot.get_command("schedule"), channel=None, schedule=schedule, text=text)
    
  @commands.group(
    name="msg",
    brief="Manages messages in db",
    help="The subcommands contain the methods to manipulate messages stored in bot's database.",
    aliases=["message"],
    case_insensitive = True,
    invoke_without_command=True
  )
  @commands.has_permissions(read_messages=True, read_message_history=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, read_message_history=True, manage_messages=True)
  @has_mod_role()
  async def _msg(self, context):
    await context.send_help("msg")
    
  @_msg.command(
    name="save",
    brief="Saves message(s) to db",
    usage="[@mentions]... [#channel] [number=1] [skip_num=0]",
    help="Saves n messages from member(s) in a channel with skipping m messages, if member is not specified it will save any message, if channel is not specified it will search the current channel.",
    aliases=["record", "archive"],
  )
  @commands.has_permissions(read_messages=True, read_message_history=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, read_message_history=True, manage_messages=True)
  @has_mod_role()
  async def _save_msg(self, context, members:commands.Greedy[discord.Member], channel:typing.Optional[discord.TextChannel], num:typing.Optional[int]=1, skip_num:typing.Optional[int]=0):
    if num <= 0:
      raise commands.UserInputError("num must be a positive number.")
    if channel is None:
      channel = context.channel
    saved = 0
    msg_count = 0
    async for message in channel.history():
      if saved >= num:
        break
      if message.id == context.message.id:
        continue # skip the command
      if len(members) == 0 or message.author in members:
        msg_count += 1
        if msg_count > skip_num: # skip the first m messages
          await save_message(self.bot, message)
          saved += 1
    title = f"Messages have been saved"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Author(s)":"\n".join([f"{member.mention} {member}" for member in members]) if members else None,
              "Channel":channel.mention,
              "Saved":f"{saved} message(s)"}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    if saved == 0:
      await send_temp_message(context, "Could not save message(s).", 10)
    await context.message.delete()
    
  @_msg.command(
    name="fetch",
    brief="Fetches a message in db",
    help="Sends the content of a message in db given its id."
  )
  @commands.has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @has_mod_role()
  async def _fetch_msg(self, context, messageID:int):
    result = self.bot.db[context.guild.id].select("messages", messageID)
    if not result:
      await context.send("Message not found.")
      return
    content, embed, files = get_full_message(result, self.bot, context.guild)
    await context.send(content=content, embed=embed, files=files)
    title = f"User fetched a message"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Message ID":messageID}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    
  @_msg.command(
    name="delete",
    brief="Deletes a message in db",
    help="Deletes a message in db given its id.",
    aliases=["del"]
  )
  @commands.has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @has_mod_role()
  async def _delete_msg(self, context, messageID:int):
    result = self.bot.db[context.guild.id].select("messages", messageID)
    if not result:
      await context.send("Message not found.")
      return
    clean_message_files(result)
    self.bot.db[context.guild.id].delete_row("messages", messageID)
    await context.send(f"Message with ID {messageID} is deleted")
    title = f"User deleted a message"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Message ID":messageID}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    
  @_msg.command(
    name="search",
    brief="Searches messages in db",
    help="Searches messages in db given a few criterions. Limit is the max number of searches. HasFile flag (True/False) specifies whether there is a file in the message. Time determines the order of the message, if it's specified the messages with time closest will be ordered first, otherwise the newest messages will be ordered first. It has to be in \"%d%h%m%s\" format (treated as a past time) or a formatted absolute time. Pattern to match the content is used with a LIKE operator thus wildcards can be used.",
    usage="[@mentions]... [#channels]... [limit=10] [hasFile] [time] [pattern]"
  )
  @commands.has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @has_mod_role()
  async def _search_msg(self, context, members:commands.Greedy[discord.Member], channels:commands.Greedy[discord.TextChannel], 
                        limit:typing.Optional[int]=10, hasFile:typing.Optional[bool]=None, date:typing.Optional[PastTimeConverter]=None, 
                        *, pattern=None):
    where_clause = []
    if members:
      in_list = ", ".join(str(member.id) for member in members)
      where_clause.append(f"aid IN ({in_list})")
    if channels:
      in_list = ", ".join(str(channel.id) for channel in channels)
      where_clause.append(f"cid IN ({in_list})")
    if hasFile is not None:
      where_clause.append("length(files)>2" if hasFile else "length(files)<=2")
    if pattern:
      where_clause.append(f"content LIKE '{pattern}'")
    if where_clause:
      where_clause = " AND ".join(where_clause)
    else:
      where_clause = "TRUE"
    if date:
      order_clause = f"ABS({date.timestamp()}-time)"
    else:
      order_clause = "time DESC"
    result = self.bot.db[context.guild.id].query(f"SELECT * FROM messages WHERE {where_clause} ORDER BY {order_clause} LIMIT {limit}")
    if not result:
      await context.send("Message not found.")
      return
    embed = discord.Embed(title=f"Message Search Results", colour=discord.Colour.green(), timestamp=context.message.created_at)
    for i in range(len(result)):
      embed.add_field(name=f"Message {i+1}:", value=get_message_brief(result[i], self.bot, context.guild))
    await context.send(embed=embed)
    
  @_msg.command(
    name="purge",
    brief="Purges messages in db",
    help="Deletes all messages in db matching the channel and author criterions before a date. Time has to be in \"%d%h%m%s\" format (treated as a past time) or a formatted absolute time.",
    usage="[@mentions]... [#channels]... [timeBefore]"
  )
  @commands.has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @commands.bot_has_permissions(read_messages=True, read_message_history=True, send_messages=True, manage_messages=True)
  @has_mod_role()
  async def _purge_msg(self, context, members:commands.Greedy[discord.Member], channels:commands.Greedy[discord.TextChannel], 
                        date:typing.Optional[PastTimeConverter]):
    where_clause = []
    if members:
      in_list = ", ".join(str(member.id) for member in members)
      where_clause.append(f"aid IN ({in_list})")
    if channels:
      in_list = ", ".join(str(channel.id) for channel in channels)
      where_clause.append(f"cid IN ({in_list})")
    if date:
      where_clause.append(f"time<={date.timestamp()}")
    if where_clause:
      where_clause = " AND ".join(where_clause)
    else:
      where_clause = "TRUE"
    # check how many messages will be deleted
    num = self.bot.db[context.guild.id].query(f"SELECT COUNT(*) FROM messages WHERE {where_clause}")
    if not num or num[0][0]==0:
      await context.send("Message not found.")
      return
    confirm, msg = await wait_user_confirmation(context, f"{num[0][0]} message(s) will be deleted, do you want to process?")
    if not confirm:
      await context.send("Operation cancelled.")
      return
    # delete all the files
    result = self.bot.db[context.guild.id].query(f"SELECT * FROM messages WHERE {where_clause} AND length(files)>2")
    if result:
      for row in result:
        clean_message_files(row)
    # delete the messages in db
    self.bot.db[context.guild.id].query(f"DELETE FROM messages WHERE {where_clause}")
    await context.send(f"{num[0][0]} message(s) have been deleted.")
    title = f"User purged messages"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Author(s)":"\n".join([member.mention for member in members]) if members else None,
              "Channel(s)":"\n".join([channel.mention for channel in channels]) if channels else None,
              "Before":date.strftime('%Y-%m-%d %H:%M:%S %z') if date else None,
              "Num":f"{num[0][0]} message(s)"}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)

def setup(bot):
  bot.add_cog(MessageManagementCog(bot))
  print("Added message management.")
