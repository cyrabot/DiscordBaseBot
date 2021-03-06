from discord.ext import commands

def is_server_owner():
  async def predicate(context):
    return context.author.id == context.guild.owner.id
  return commands.check(predicate)

def has_admin_role():
  async def predicate(context):
    if context.author.id in context.bot.owner_ids:
      return True
    admin_role = context.bot.get_admin_role(context.guild)
    if admin_role is None:
      return False
    else:
      return admin_role in context.author.roles
  return commands.check(predicate)
  
def has_mod_role():
  async def predicate(context):
    if context.author.id in context.bot.owner_ids:
      return True
    mod_role = context.bot.get_mod_role(context.guild)
    if mod_role is None:
      return False
    else:
      return mod_role in context.author.roles
  return commands.check(predicate)
  
def can_edit_commands():
  async def predicate(context):
    if context.author.id in context.bot.owner_ids:
      return True
    admin_role = context.bot.get_admin_role(context.guild)
    cmd_role = context.bot.get_cmd_role(context.guild)
    if admin_role is None and cmd_role is None:
      return False
    else:
      return admin_role in context.author.roles or cmd_role in context.author.roles
  return commands.check(predicate)

