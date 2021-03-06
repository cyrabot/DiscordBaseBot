from datetime import datetime
import json
import os
import discord
from discord.ext import commands
from base.modules.access_checks import has_mod_role, has_admin_role
from base.modules.constants import CACHE_PATH as path
from base.modules.message_helper import save_message

class ChannelManagementCog(commands.Cog, name="Channel Management Commands"):
  def __init__(self, bot):
    self.bot = bot
    self.monitor = {}
    if not os.path.isdir(path):
      os.mkdir(path)
    try:
      with open(f"{path}/monitor.json") as f:
        data = json.load(f)
        if isinstance(data, dict):
          for key in data:
            if isinstance(data[key], list):
              self.monitor[int(key)] = data[key]
    except:
      pass
    for guild in self.bot.guilds:
      self.init_guild(guild)
    
  def init_guild(self, guild):
    if guild.id not in self.monitor:
      self.monitor[guild.id] = []
  
  def cog_unload(self):
    try:
      with open(f'{path}/monitor.json', 'w') as f:
        json.dump(self.monitor, f)
    except:
      pass

  async def cog_command_error(self, context, error):
    if hasattr(context.command, "on_error"):
      # This prevents any commands with local handlers being handled here.
      return
    if isinstance(error, commands.MissingPermissions):
      await context.send(f"Sorry {context.author.mention}, but you do not have permission to execute that command!")
    elif isinstance(error, commands.BotMissingPermissions):
      await context.send(f"Sorry {context.author.mention}, but I do not have permission to execute that command!")
    elif isinstance(error, commands.UserInputError):
      await context.send(f"Sorry {context.author.mention}, but I could not understand the arguments passed to `?{context.command.qualified_name}`.")
    elif isinstance(error, commands.CheckFailure):
      await context.send(f"Sorry {context.author.mention}, but you do not have permission to execute that command!")
    else:
      await context.send(f"Sorry {context.author.mention}, something unexpected happened while executing that command.")
      
  @commands.Cog.listener()
  async def on_message(self, message):
    if message.channel.id in self.monitor[message.channel.guild.id]:
      await save_message(self.bot, message)

  @commands.group(
    name="channel",
    brief="Channel management",
    invoke_without_command=True,
    aliases=["ch"]
  )
  @commands.has_permissions(manage_channels=True)
  @commands.bot_has_permissions(manage_channels=True)
  @has_mod_role()
  async def _channel(self, context):
    await context.send_help("channel")

  @_channel.command(
    name="close",
    brief="Makes Channel invisible",
  )
  @commands.has_permissions(manage_channels=True)
  @commands.bot_has_permissions(manage_channels=True)
  @has_mod_role()
  async def _channel_close(self, context, members: commands.Greedy[discord.Member] = [], roles: commands.Greedy[discord.Role] = []):
    overwrites = discord.PermissionOverwrite(view_channel = False)
    permissions = context.message.channel.overwrites
    #bot still needs access to the channel
    permissions[context.guild.me] = discord.PermissionOverwrite(
      create_instant_invite=True, manage_channels=True, manage_roles=True, manage_webhooks=True, read_messages=True,
      send_messages=True, send_tts_messages=True, manage_messages=True, embed_links=True, 
      attach_files=True, read_message_history=True, mention_everyone=True, use_external_emojis=True, add_reactions=True
    )
    targets = []
    if len(members) == len(roles) == 0:
      permissions[context.guild.default_role] = overwrites
    else:
      for member in members:
        permissions[member] = overwrites
        targets.append(f"{member.mention}\n{member}")
      for role in roles:
        permissions[role] = overwrites
        targets.append(f"{role.mention}")
    await context.message.channel.edit(overwrites=permissions)
    title = "Channel Closed"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":f"{context.message.channel.mention}\n{context.message.channel}",
              "Targets":"\n".join(targets) if targets else None}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)

  @_channel.command(
    name="open",
    brief="Makes Channel visible",
    aliases=["unmute"]
  )
  @commands.has_permissions(manage_channels=True)
  @commands.bot_has_permissions(manage_channels=True)
  @has_mod_role()
  async def _channel_open(self, context, members: commands.Greedy[discord.Member] = [], roles: commands.Greedy[discord.Role] = []):
    overwrites = discord.PermissionOverwrite(
      create_instant_invite=True, manage_channels=False, manage_roles=False, manage_webhooks=False, read_messages=True,
      send_messages=True, send_tts_messages=True, manage_messages=False, embed_links=True, 
      attach_files=True, read_message_history=True, mention_everyone=True, use_external_emojis=True, add_reactions=True
    )
    permissions = context.message.channel.overwrites
    targets = []
    if context.guild.me in permissions:
      permissions.pop(context.guild.me, None)
    else:
      permissions[context.guild.me] = overwrites
    if len(members) == len(roles) == 0:
      if context.guild.default_role in permissions:
        permissions.pop(context.guild.default_role, None)
      else:
        permissions[context.guild.default_role] = overwrites
    else:
      for member in members:
        if member in permissions:
          if permissions[member] != overwrites:
            permissions.pop(member, None)
        else:
          permissions[member] = overwrites
        targets.append(f"{member.mention}\n{member}")
      for role in roles:
        if role in permissions:
          if permissions[role] != overwrites:
            permissions.pop(role, None)
        else:
          permissions[role] = overwrites
        targets.append(f"{role.mention}")
    await context.message.channel.edit(overwrites=permissions)
    title = "Channel Opened"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":f"{context.message.channel.mention}\n{context.message.channel}",
              "Targets":"\n".join(targets) if targets else None}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)

  @_channel.command(
    name="mute",
    brief="Disables messaging",
  )
  @commands.has_permissions(manage_channels=True)
  @commands.bot_has_permissions(manage_channels=True)
  @has_mod_role()
  async def _channel_mute(self, context, members: commands.Greedy[discord.Member] = [], roles: commands.Greedy[discord.Role] = []):
    overwrites = discord.PermissionOverwrite(
      view_channel=True, send_messages=False, send_tts_messages=False, manage_messages=False, embed_links=False, 
      attach_files=False, read_message_history=True, mention_everyone=False, use_external_emojis=True, add_reactions=True
    )
    permissions = context.message.channel.overwrites
    #bot still needs access to the channel
    permissions[context.guild.me] = discord.PermissionOverwrite(
      create_instant_invite=True, manage_channels=True, manage_roles=True, manage_webhooks=True, read_messages=True,
      send_messages=True, send_tts_messages=True, manage_messages=True, embed_links=True, 
      attach_files=True, read_message_history=True, mention_everyone=True, use_external_emojis=True, add_reactions=True
    )
    targets = []
    if len(members) == len(roles) == 0:
      permissions[context.guild.default_role] = overwrites
    else:
      for member in members:
        permissions[member] = overwrites
        targets.append(f"{member.mention}\n{member}")
      for role in roles:
        permissions[role] = overwrites
        targets.append(f"{role.mention}")
    await context.message.channel.edit(overwrites=permissions)
    title = "Channel Muted"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":f"{context.message.channel.mention}\n{context.message.channel}",
              "Targets":"\n".join(targets) if targets else None}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    
  @_channel.group(
    name="monitor",
    brief="Starts monitoring channel",
    help="Starts monitoring the current channel by saving all the coming messages ",
    invoke_without_command=True,
  )
  @commands.has_permissions(manage_channels=True)
  @commands.bot_has_permissions(manage_channels=True)
  @has_mod_role()
  async def _channel_monitor(self, context):
    if context.message.channel.id in self.monitor[context.guild.id]:
      await context.send("This channel is already under monitor.")
      return
    self.monitor[context.guild.id].append(context.message.channel.id)
    await context.send("Started monitoring this channel.")
    title = "Start Monitoring Channel"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":f"{context.message.channel.mention}\n{context.message.channel}"}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)
    
  @_channel_monitor.command(
    name="off",
    brief="Stops monitoring channel",
  )
  @commands.has_permissions(manage_channels=True)
  @commands.bot_has_permissions(manage_channels=True)
  @has_mod_role()
  async def _channel_monitor_off(self, context):
    if context.message.channel.id not in self.monitor[context.guild.id]:
      await context.send("This channel is not under monitor.")
      return
    self.monitor[context.guild.id].remove(context.message.channel.id)
    await context.send("Stopped monitoring this channel.")
    title = "Stop Monitoring Channel"
    fields = {"User":f"{context.author.mention}\n{context.author}",
              "Channel":f"{context.message.channel.mention}\n{context.message.channel}"}
    await self.bot.log_mod(context.guild, title=title, fields=fields, timestamp=context.message.created_at)

def setup(bot):
  bot.add_cog(ChannelManagementCog(bot))
  print("Added channel management.")
