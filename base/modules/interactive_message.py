import discord
from abc import ABC, abstractmethod
import asyncio
from base.modules.constants import arrow_emojis

class InteractiveMessage(ABC):
  
  # Abstract class for interactive message
  def __init__(self, parent=None, **attributes):
    self.inherited = False # if true, the object inherits emojis and transfer methods from its parent
    self.parent = parent # the parent message
    self.parent_emoji = arrow_emojis["back"] # the emoji to go backward
    if parent is not None:
      self.timeout = parent.timeout # how long will the message be active
      self.context = parent.context # the context that starts the message
      self.message = parent.message # the discord message bonded to this object
    else:
      self.timeout = attributes.pop("timeout", None)
      self.context = attributes.pop("context", None)
      self.message = attributes.pop("message", None)
    self.child_emojis = [] # all the emojis that will make the message transfer to its child
    self.construct_emoji = None # the emoji that makes this message
    self.last_msg = None # the last message that transfer to this message, could be the parent or other
    
  @property
  def accept_emojis(self): # a set of emojis accepted by the message
    emojis = []
    if self.parent is not None and self.parent_emoji:
      emojis.append(self.parent_emoji)
    if self.parent is not None and self.inherited:
      emojis.extend(self.parent.child_emojis)
    emojis.extend(self.child_emojis)
    return list(dict.fromkeys(emojis))
    
  @abstractmethod
  async def transfer_to_child(self, emoji):
    '''
    How to transfer to a child interactive message when a emoji reaction occurs
    You can either change the self property and return self or construct a new InteractiveMessage object
    If you return None here the message won't get updated even if the object itself is updated
    Returns a subclass of InteractiveMessage or None if no need to transfer
    '''
    pass
    
  # you have to override at least one method below to aviod empty contents
  async def get_content(self): # return some content
    pass
  
  async def get_embed(self): # return an embed
    pass
  
  async def get_file(self): # return a file
    pass
  
  async def transfer(self, emoji):
    # how to transfer to a new interactive message when a emoji reaction occurs
    # return None if no need to transfer
    if emoji in self.child_emojis:
      new_msg = await self.transfer_to_child(emoji)
      if new_msg is not None:
        new_msg.construct_emoji = emoji
    elif self.parent is not None and self.inherited and emoji in self.parent.child_emojis:
      new_msg = await self.parent.transfer(emoji)
      if new_msg is not None:
        new_msg.construct_emoji = emoji
    elif self.parent is not None and emoji == self.parent_emoji:
      new_msg = self.parent
    else:
      return None
    return new_msg
    
  async def respond_message(self, msg=None): # send the embed
    _content, _embed, _file = await self.get_content(), await self.get_embed(), await self.get_file()
    if msg is None or msg.author.id != self.context.bot.user.id:
      self.message = await self.context.send(content=_content, embed=_embed, file=_file)
    else:
      self.message = msg
      await self.message.clear_reactions()
      await self.message.edit(content=_content, embed=_embed, file=_file)
    parent = self.parent
    while parent is not None and parent.message is None:
      # transfer the message up if not initialized
      parent.message = self.message
      parent = parent.parent
    for emoji in self.accept_emojis:
      await self.message.add_reaction(emoji)
    
  async def update_message(self): # update the current message embed
    _content, _embed, _file = await self.get_content(), await self.get_embed(), await self.get_file()
    await self.message.edit(content=_content, embed=_embed, file=_file)
      
  async def wait_for_reaction(self): # wait for the next reaction and update the message
    accept_emojis = self.accept_emojis
    def check(reaction, user):
      return user == self.context.message.author and reaction.message.id == self.message.id and reaction.emoji in accept_emojis
    reaction, user = await self.context.bot.wait_for('reaction_add', timeout=self.timeout, check=check)
    newInteractiveMessage = await self.transfer(reaction.emoji)
    if newInteractiveMessage is None:# nothing changed, remove the reaction
      newInteractiveMessage = self
      await reaction.remove(user)
    else: # update the message and change the reactions
      await newInteractiveMessage.update_message()
      if accept_emojis == newInteractiveMessage.accept_emojis:
        await reaction.remove(user)
      else:
        await self.message.clear_reactions()
        for emoji in newInteractiveMessage.accept_emojis:
          await self.message.add_reaction(emoji)
    return newInteractiveMessage
    
  async def start(self, msg=None): # start the message, send the embed, and loop for waiting a reaction
    await self.respond_message(msg)
    while True:
      try:
        self = await self.wait_for_reaction()
      except asyncio.TimeoutError:
        await self.message.clear_reactions()
        break
        
class DetermInteractiveMessage(InteractiveMessage, ABC):
  # The same as InteractiveMessage except it won't transform if it's construct_emoji is the same as transform emoji when inheriting from parent
  def __init__(self, parent=None, **attributes):
    super().__init__(parent, **attributes)
    
  async def transfer(self, emoji):
    # how to transfer to a new interactive message when a emoji reaction occurs
    # return None if no need to transfer
    if emoji in self.child_emojis:
      new_msg = await self.transfer_to_child(emoji)
      if new_msg is not None:
        new_msg.construct_emoji = emoji
    elif self.parent is not None and self.inherited and emoji in self.parent.child_emojis:
      if self.construct_emoji == emoji:
        return None
      new_msg = await self.parent.transfer(emoji)
      if new_msg is not None:
        new_msg.construct_emoji = emoji
    elif self.parent is not None and emoji == self.parent_emoji:
      new_msg = self.parent
    else:
      return None
    return new_msg
