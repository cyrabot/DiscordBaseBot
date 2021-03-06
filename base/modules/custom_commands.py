import discord
from discord.ext import commands
from pytz import timezone
import operator
import json

def json_load_dict(string):
  if string:
    try:
      dic = json.loads(string)
      if not isinstance(dic, dict):
        dic = {}
    except:
      dic = {}
  else:
    dic = {}
  return dic

def get_cmd_parent_child(root, cmd_name):
  cmd_name_split = cmd_name.split(maxsplit=1)
  if len(cmd_name_split) == 1:
    return (root, cmd_name)
  else:
    parent = cmd_name_split[0]
    child = cmd_name_split[1]
    parent_cmd = root.get_command(parent)
    if parent_cmd is None:
      raise NameError(f"Command is invalid because its parent command "
        f"'{root.qualified_name+' ' if hasattr(root, 'qualified_name') else ''}{parent}' does not exists.")
    if parent_cmd.cog_name:
      # would it be better to check the db or subclass custom commmands to check it?
      raise NameError(f"Command is invalid because its parent command "
        f"'{parent_cmd.qualified_name}' is in cog {parent_cmd.cog_name} and not customizable.")
    if not isinstance(parent_cmd, commands.Group):
      raise NameError(f"Command is invalid because its parent command "
        f"'{parent_cmd.qualified_name}' is not a command group.")
    return get_cmd_parent_child(parent_cmd, child)
    
def get_cmd_attributes(bot, cmd_name, expected, check_child=False):
  # find the parent command (or bot) and map the command (alias) name to the qualified name
  parent, child = get_cmd_parent_child(bot, cmd_name)
  existing_cmd = parent.get_command(child)
  if expected:
    if existing_cmd is None:
      raise NameError(f"Command '{cmd_name}' does not exist.")
    else:
      if check_child and isinstance(existing_cmd, commands.Group) and len(existing_cmd.commands) > 0:
        raise LookupError(f"Command '{cmd_name}' cannot be removed because it has at least one child command.")
      child_name = existing_cmd.name
      qualified_name = existing_cmd.qualified_name
  if not expected:
    if existing_cmd is not None:
      raise NameError(f"Command '{cmd_name}' already exists.")
    else:
      child_name = child
      qualified_name = f"{parent.qualified_name} {child}" if hasattr(parent, "qualified_name") else child
  return (parent, child_name, qualified_name)
    
     
def make_user_command(cmd_name, cmd_text, **attributes):
  @commands.command(
    name=cmd_name,
    description="Usage"
  )
  async def _wrapper_user_cmd(context):
    await context.send(cmd_text)
  _wrapper_user_cmd.update(**attributes)
  return _wrapper_user_cmd
    
def make_user_group(cmd_name, cmd_text, **attributes):
  @commands.group(
    name=cmd_name,
    invoke_without_command=True,
    case_insensitive=True,
    description="Usage"
  )
  async def _wrapper_user_cmd(context):
    if cmd_text:
      await context.send(cmd_text)
    else:
      await context.send_help(context.command)
  _wrapper_user_cmd.update(**attributes)
  return _wrapper_user_cmd
  
def fix_aliases(parent, child_name, aliases):
  # fix the aliases of a command to remove any duplicates
  if parent.case_insensitive:
    new_aliases = [alias.lower() for alias in aliases]
  else:
    new_aliases = aliases
  new_aliases = list(set(new_aliases))
  new_aliases = [alias for alias in new_aliases if (parent.get_command(alias) is None and alias != child_name)]
  return new_aliases
  
def smart_add_command(parent, cmd):
  # add the command smartly to remove duplicates of aliases
  new_aliases = fix_aliases(parent, cmd.name, cmd.aliases)
  cmd.update(aliases=new_aliases)
  parent.add_command(cmd)

def check_aliases(parent, child_name, aliases):
  # child_name is the name of the child to be updated, which will be ignored
  if parent.case_insensitive:
    aliases = [alias.lower() for alias in aliases]
  aliases_set = set()
  for alias in aliases:
    cmd = parent.get_command(alias)
    if cmd is not None and (cmd.name != child_name or cmd.name == alias):
      raise commands.CommandRegistrationError(alias, alias_conflict=True)
    if alias in aliases_set:
        raise commands.CommandRegistrationError(alias, alias_conflict=True)
    else:
        aliases_set.add(alias)
      
def move_subcommands(old_cmd, new_cmd):
  if isinstance(old_cmd, commands.Group) and isinstance(new_cmd, commands.Group):
    for command in old_cmd.commands: # move the commands from the old one to the new one
      if (not old_cmd.case_insensitive) and new_cmd.case_insensitive:
        # if case_insensitive has changed to False, fix the aliases of subcommands by using smart add
        smart_add_command(new_cmd, command)
      else:
        new_cmd.add_command(command)

def set_new_cmd(parent, child_name, cmd_text, attributes, is_group=False, smart_fix=False):
  # check aliases here
  if "aliases" in attributes:
    if smart_fix:
      attributes["aliases"] = fix_aliases(parent, child_name, attributes["aliases"])
    # make sure the aliases are unique
    else:
      check_aliases(parent, child_name, attributes["aliases"])
  old_cmd = parent.remove_command(child_name) # remove the command if exits, it doesn't hurt if the command does not exist
  if is_group:
    usr_command = make_user_group(child_name, cmd_text, **attributes)
  else:
    usr_command = make_user_command(child_name, cmd_text, **attributes)
  parent.add_command(usr_command)
  move_subcommands(old_cmd, usr_command)
  return usr_command
  
def add_cmd_from_row(bot, cmd):
  attributes = json_load_dict(cmd["attributes"])
  parent, child, cmd_name = get_cmd_attributes(bot, cmd["cmdname"], False)
  set_new_cmd(parent, child, cmd["message"], attributes, cmd["isgroup"], True)
  
