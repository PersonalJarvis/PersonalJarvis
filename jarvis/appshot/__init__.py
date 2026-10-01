"""Appshots — show the assistant the window you are working in.

An appshot is one capture of the front window, taken on a global shortcut
(both Alt keys by default), from the Appshots page, or when the user asks for
one, and handed to the conversation as context: straight into a running voice
call, otherwise onto the next message. Capture, privacy filtering and
retention are the Screen Context engine's (``jarvis/screen_context``); this
package adds the triggers, the delivery, the shutter effect and the sound
gate. Nothing here is imported at boot (AP-26). See ``docs/appshots.md``.
"""
