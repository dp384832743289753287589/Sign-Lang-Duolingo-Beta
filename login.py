from argon2 import PasswordHasher # for hashing passwords securely. better than hashlib.sha256 because it's slower
from argon2.exceptions import VerifyMismatchError # error handling for incorrect passwords
import pandas as pd     # imported but not used yet -- you had it in your original imports
import tkinter as tk    # the GUI library; we alias it as "tk" so we type tk.Button instead of tkinter.Button
from tkinter import messagebox   # messagebox is a sub-module -- handles those popup dialogs
import subprocess       # imported but not used yet -- could later be used to launch main.py as a subprocess
import os               # lets us build file paths and check if files exist
import csv              # lets us read and write .csv files (where users are stored)
import sys              # gives us sys.exit() to kill the whole program when needed
import time             # gives us time.time() -- current time as a unix timestamp (seconds since 1970)
import secrets          # better than random for security stuff -- used to generate the session token

# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────

# __file__ is a built-in Python variable -- the path to THIS script
# os.path.abspath() makes it absolute (not relative), so it works no matter where you run from
# os.path.dirname() strips the filename off the end, leaving just the folder
THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# builds the full path to users.csv by joining the folder + filename
# os.path.join handles the slashes correctly on Windows/Mac/Linux automatically
USERS_FILE = os.path.join(THIS_DIR, "users.csv")

# same idea -- this is where the session token gets written after login
# the dot prefix is a convention for "hidden/internal" files, not enforced by Python
TOKEN_FILE = os.path.join(THIS_DIR, ".auth_token")

# ─────────────────────────────────────────────
# Auth token helpers
# ─────────────────────────────────────────────

TOKEN_EXPIRY_SECONDS = 18000   # 18000 -- token is valid for 5 hours; change this to whatever you want

def progress_check():
    pass
def write_auth_token(username: str):   # takes the logged-in username as a string
    token = secrets.token_hex(32)      # generates 32 random bytes, returned as a 64-char hex string -- this is the actual token
    timestamp = str(time.time())       # current time as a float like "1719523200.45", converted to string so we can write it to a file
    # open the token file in write mode ("w") -- creates it if it doesn't exist, overwrites if it does
    with open(TOKEN_FILE, "w") as f:
        f.write(f"{token}|{timestamp}|{username}")   # writes all three pieces separated by | so we can split them back out later


def validate_auth_token() -> str | None:   # return type hint: returns either a string (username) or None
    if not os.path.exists(TOKEN_FILE):     # if the file doesn't exist at all, nobody has logged in
        return None                        # return None to signal "not authenticated"
    try:                                   # wrap everything in try/except -- if the file is corrupted or weird, we just say "invalid"
        with open(TOKEN_FILE, "r") as f:   # open for reading
            parts = f.read().strip().split("|")   # read the whole file, strip whitespace, split on | into a list of 3 parts
        if len(parts) != 3:       # if we don't get exactly 3 parts, something is wrong with the file
            return None
        _, timestamp, username = parts    # unpack -- _ means "we don't need the token value itself here, just timestamp and username"
        age = time.time() - float(timestamp)    # how many seconds ago was the token written?
        if age > TOKEN_EXPIRY_SECONDS:          # if it's older than 1 hour, it's expired
            os.remove(TOKEN_FILE)              # delete the stale token file so it doesn't linger
            return None                        # and signal "not authenticated"
        return username    # if we made it here, the token is valid and fresh -- return the username
    except Exception:      # catches ANY error (bad file, bad float conversion, etc)
        return None        # treat any error as "not authenticated" rather than crashing


def require_auth():    # this is what main.py and camera.py call at the very top
    user = validate_auth_token()    # check if a valid token exists
    if user is None:                # if not...
        print("─" * 50)
        print("  ACCESS DENIED: You must log in first.")   # tell them what happened
        print("  Run:  python login.py")                   # tell them what to do
        print("─" * 50)
        sys.exit(1)    # exit the whole program immediately; 1 = "exited with an error" by convention (0 = clean exit)
    print(f"✅ Authenticated as: {user}")   # if we're here, auth passed -- confirm who's logged in
    return user    # return the username in case the calling script wants to use it

# ─────────────────────────────────────────────
# Password hashing
# ─────────────────────────────────────────────

ph = PasswordHasher()   # Initialize the Argon2 hasher

def hash_password(password: str) -> str:
    # ph.hash creates a secure argon2 hash complete with a unique salt
    return ph.hash(password)

# ─────────────────────────────────────────────
# User storage
# ─────────────────────────────────────────────

def load_users() -> dict:    # return type hint: returns a dictionary
    if not os.path.exists(USERS_FILE):   # if no users file yet, nobody has signed up
        return {}                        # return an empty dict -- no users
    users = {}                           # start with an empty dict to fill in
    with open(USERS_FILE, newline="") as f:      # newline="" is required by the csv module on Windows to avoid double line breaks
        reader = csv.DictReader(f)               # DictReader reads each row as a dict using the header row as keys
        for row in reader:                       # loop through every user row
            users[row["username"]] = row["password_hash"]   # build {username: hashed_password}
    return users    # hand back the complete dict of all users


def save_user(username: str, hashed_password: str):    # called once when someone signs up
    file_exists = os.path.exists(USERS_FILE)           # check BEFORE opening -- once we open it, it'll exist regardless
    with open(USERS_FILE, "a", newline="") as f:       # "a" = append mode -- adds to the end, doesn't overwrite existing users
        writer = csv.DictWriter(f, fieldnames=["username", "password_hash"])   # DictWriter lets us write dicts as rows
        if not file_exists:          # only write the header row if this is a brand new file
            writer.writeheader()     # writes "username,password_hash" as the first line
        writer.writerow({"username": username, "password_hash": hashed_password})   # writes the actual new user row

# ─────────────────────────────────────────────
# Tkinter UI
# ─────────────────────────────────────────────

class LoginApp:                          # a class lets us keep all the UI state (entries, mode, etc) in one place via self
    def __init__(self, root: tk.Tk):     # __init__ runs when you do LoginApp(root) -- root is the main window
        self.root = root                             # save the window reference so other methods can use it
        self.root.title("Sign In ")                   # sets the text in the OS title bar
        self.root.geometry("760x640")                # sets the window size in pixels: width x height
        self.root.resizable(True, True)            # locks the window size -- False for width, False for height
        self.root.configure(bg="#0e1117")            # sets the background color (dark theme)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # WM_DELETE_WINDOW is the event that fires when the user clicks the X
        # instead of tkinter's default (just close), we redirect it to our own method

        self.mode = tk.StringVar(value="login")   # StringVar is a tkinter variable that widgets can watch for changes
                                                  # starts as "login", will switch to "signup" when they click Sign Up
        self._build_ui()    # call our method that actually creates all the widgets

    def _on_close(self):    # called when the user clicks the X button
        # asks "are you sure?" -- returns True if they click OK
        if messagebox.askokcancel("Quit", "Cancel login and exit the program?"):
            sys.exit(0)     # 0 = intentional/clean exit (as opposed to 1 for errors)

    def _build_ui(self):
        # ── Title label ──
        tk.Label(self.root,                        # Label is just static text; self.root = attach it to the main window
                 text="🤟 Sign Language Duolingo ",
                 font=("Helvetica", 16, "bold"),
                 bg="#0e1117",                     # background must match the window or it looks like a box
                 fg="#fafafa"                      # fg = foreground = text color
                 ).pack(pady=(24, 4))              # .pack() places it; pady=(top, bottom) adds vertical spacing

        # ── Toggle buttons row ──
        toggle_frame = tk.Frame(self.root, bg="#0e1117")   # Frame is an invisible container -- groups the two tab buttons together
        toggle_frame.pack(pady=(0, 16))                    # pack the frame itself into the window

        self.login_tab = tk.Button(toggle_frame,           # Button inside the frame, not the root window
                                   text="Log In",
                                   width=10,               # width in characters
                                   command=lambda: self._switch_mode("login"),   # lambda wraps the call so it doesn't fire immediately
                                   relief="flat",          # removes the 3D border look
                                   font=("Helvetica", 11, "bold"))
        self.login_tab.grid(row=0, column=0, padx=4)      # grid() places it in column 0 of the frame

        self.signup_tab = tk.Button(toggle_frame,
                                    text="Sign Up",
                                    width=10,
                                    command=lambda: self._switch_mode("signup"),
                                    relief="flat",
                                    font=("Helvetica", 11, "bold"))
        self.signup_tab.grid(row=0, column=1, padx=4)     # column 1 = sits to the right of the login tab

        # ── Form fields ──
        form = tk.Frame(self.root, bg="#0e1117")   # another invisible container, this time for the input fields
        form.pack()

        tk.Label(form, text="Username", bg="#0e1117", fg="#aaaaaa",
                 font=("Helvetica", 10)).grid(row=0, column=0, sticky="w", pady=(0, 2))
        # sticky="w" means align to the West (left) edge of its grid cell

        self.username_entry = tk.Entry(form, width=28, font=("Helvetica", 11))   # Entry is a single-line text input
        self.username_entry.grid(row=1, column=0, pady=(0, 10))   # goes in the row below the label

        tk.Label(form, text="Password", bg="#0e1117", fg="#aaaaaa",
                 font=("Helvetica", 10)).grid(row=2, column=0, sticky="w", pady=(0, 2))

        self.password_entry = tk.Entry(form, width=28, show="*", font=("Helvetica", 11))
        # show="*" masks the characters -- every letter shows as * (this is what makes it a password field)
        self.password_entry.grid(row=3, column=0, pady=(0, 10))

        # These two are created but NOT added to the grid yet -- _switch_mode controls when they appear
        self.confirm_label = tk.Label(form, text="Confirm Password", bg="#0e1117",
                                      fg="#aaaaaa", font=("Helvetica", 10))
        self.confirm_entry = tk.Entry(form, width=28, show="*", font=("Helvetica", 11))

        # ── Submit button ──
        self.submit_btn = tk.Button(self.root,
                                    text="Log In",
                                    width=20,
                                    command=self._submit,    # no lambda needed here -- _submit takes no arguments
                                    font=("Helvetica", 11, "bold"),
                                    bg="#ff4b4b",            # red background
                                    fg="white",
                                    relief="flat",
                                    cursor="hand2")          # changes mouse cursor to a pointer on hover
        self.submit_btn.pack(pady=(8, 0))

        # ── Status/error label ──
        self.status_label = tk.Label(self.root, text="",    # starts empty -- we fill it in when there's an error
                                     bg="#0e1117", fg="#ff4b4b",   # red text for errors
                                     font=("Helvetica", 9))
        self.status_label.pack(pady=(8, 0))

        self._switch_mode("login")    # run once at startup to apply the correct initial button colors

    def _switch_mode(self, mode: str):
        self.mode.set(mode)    # update the StringVar so the rest of the class knows which mode we're in

        if mode == "login":
            self.login_tab.configure(bg="#ff4b4b", fg="white")     # active tab = red
            self.signup_tab.configure(bg="#2a2a3a", fg="#aaaaaa")  # inactive tab = dark grey
            self.confirm_label.grid_remove()    # hide the confirm field -- grid_remove() remembers its position for later
            self.confirm_entry.grid_remove()
            self.submit_btn.configure(text="Log In")
        else:
            self.signup_tab.configure(bg="#ff4b4b", fg="white")
            self.login_tab.configure(bg="#2a2a3a", fg="#aaaaaa")
            self.confirm_label.grid(row=4, column=0, sticky="w", pady=(0, 2))   # re-show it at the same position
            self.confirm_entry.grid(row=5, column=0, pady=(0, 10))
            self.submit_btn.configure(text="Sign Up")

        self.status_label.configure(text="")    # clear any old error message whenever the mode switches

    def _submit(self):
        username = self.username_entry.get().strip()   # .get() reads the current text; .strip() removes accidental spaces
        password = self.password_entry.get()           # no strip on password -- spaces might be intentional

        if not username or not password:               # empty string is falsy in Python
            self.status_label.configure(text="Username and password are required.")
            return    # return early -- don't proceed

        users = load_users()    # load the full user dict from the CSV

        if self.mode.get() == "login":
            if username not in users:                  # check if this username exists at all
                self.status_label.configure(text="No account found. Please sign up.")
                return
            try:
                # Argon2 automatically extracts the salt from the stored hash and verifies it
                ph.verify(users[username], password)
            except VerifyMismatchError:
                # If it fails, it throws this specific error
                self.status_label.configure(text="Incorrect password.")
                return
            # ── if we reach here, login succeeded ──
            write_auth_token(username)                        # write the session token to disk
            messagebox.showinfo("Welcome back!", f"Logged in as {username}.")   # show a popup
            self.root.destroy()    # close the window -- mainloop() returns after this

        else:    # signup mode
            confirm = self.confirm_entry.get()                 # read the confirm password field
            if password != confirm:                            # make sure both fields match
                self.status_label.configure(text="Passwords do not match.")
                return
            if len(password) < 6:                             # enforce a minimum length
                self.status_label.configure(text="Password must be at least 6 characters.")
                return
            if username in users:                             # don't allow duplicate usernames
                self.status_label.configure(text="Username already taken.")
                return
            # ── if we reach here, signup succeeded ──
            save_user(username, hash_password(password))      # hash the password, then save to CSV
            write_auth_token(username)                        # immediately log them in too
            messagebox.showinfo("Account created!", f"Welcome, {username}!")
            self.root.destroy()

# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":       # only runs when you do "python login.py" directly, not when imported by main.py
    root = tk.Tk()               # creates the actual OS window
    app = LoginApp(root)         # builds all the widgets inside it
    root.mainloop()              # hands control to tkinter -- sits here until root.destroy() is called
    # by the time we reach this line, the user has successfully logged in (destroy was called after auth)
    print("✅ Login complete. You can now run main.py or camera.py.")
