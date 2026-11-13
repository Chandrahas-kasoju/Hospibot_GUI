#!/usr/bin/env python3
import os
import time
import threading
import tkinter as tk
from tkinter import messagebox
import subprocess
import requests
import queue
import random
import shutil
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
TEAMS_URL = "https://teams.microsoft.com"
USER_DATA_DIR = "/home/hospibot/hospibot_ws/doctortrial/teams_profile_pi"
# Tele's mode manager runs in the ROS container and listens on this local address
MODE_URL = "http://127.0.0.1:8011/mode"

# --- Global Queues ---
command_queue = queue.Queue()
gui_queue = queue.Queue()

# --- Helper Functions for Window Management ---

def run_xdotool(args):
    """Runs xdotool commands silently."""
    try:
        subprocess.run(["xdotool"] + args, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass

def force_cleanup_profile():
    """Kills stuck chrome processes and removes the Profile Lock."""
    print("Cleaning up stuck Chromium processes...")
    try:
        subprocess.run(["pkill", "-f", "chromium"], check=False)
        subprocess.run(["pkill", "-f", "chrome"], check=False)
        time.sleep(1)
    except:
        pass

    lock_file = os.path.join(USER_DATA_DIR, "SingletonLock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("Removed stuck lock file (Self-Healing).")
        except Exception as e:
            print(f"Warning: Could not remove lock file: {e}")

def switch_to_pwa():
    """Hides GUI, Shows PWA App, and FORCES Fullscreen."""
    print("Switching to TEAMS PWA...")
    gui_queue.put(("set_view", "browser"))
    time.sleep(0.5)
    run_xdotool(["search", "--class", "chromium", "windowactivate"])
    time.sleep(0.5)
    run_xdotool(["search", "--class", "chromium", "windowstate", "--add", "fullscreen"])

def switch_to_gui():
    """Hides PWA, Shows GUI."""
    print("Switching to GUI...")
    run_xdotool(["search", "--class", "chromium", "windowstate", "--remove", "fullscreen"])
    time.sleep(0.2)
    run_xdotool(["search", "--class", "chromium", "windowminimize"])
    time.sleep(0.2)
    run_xdotool(["search", "--name", "Hospibot Pi", "windowactivate"])
    gui_queue.put(("set_view", "gui"))

def wait_for_network():
    while True:
        try:
            requests.head(TEAMS_URL, timeout=5)
            print("Network is UP.")
            return
        except:
            gui_queue.put(("status", "Waiting for Internet..."))
            time.sleep(5)

# --- BROWSER WORKER ---

def browser_worker():
    wait_for_network()
    force_cleanup_profile()
    
    while True:
        gui_queue.put(("status", "System Loading..."))
        
        try:
            with sync_playwright() as p:
                print("Launching Teams (Auto-Setup Mode)...")
                
                exec_path = "/usr/bin/chromium"
                if not os.path.exists(exec_path):
                    exec_path = "/usr/bin/chromium-browser"

                launch_args = [
                    f"--app={TEAMS_URL}",
                    #"--kiosk",
                    "--start-fullscreen", 
                    "--no-default-browser-check",
                    "--force-device-scale-factor=0.93",
                    "--use-fake-ui-for-media-stream",
                    "--disable-infobars",
                    "--disable-dev-shm-usage",
                    "--test-type"
                ]

                context = p.chromium.launch_persistent_context(
                    USER_DATA_DIR,
                    executable_path=exec_path,
                    headless=False,
                    permissions=["camera", "microphone"],
                    viewport={"width": 720, "height": 1280},
                    args=launch_args,
                    ignore_default_args=["--enable-automation"]
                )

                if len(context.pages) > 0:
                    page = context.pages[0]
                else:
                    time.sleep(1)
                    page = context.pages[0]
                
                # --- STARTUP STABILIZATION ---
                print("Stabilizing Window...")
                time.sleep(5)
                for _ in range(3):
                    switch_to_pwa()
                    time.sleep(1)
                
                # --- SMART LOGIN HANDLER ---
                if not _handle_login_flow(page):
                    continue 

                switch_to_gui()
                _internal_reset_ready(page)

                # --- COMMAND LOOP ---
                while True:
                    try:
                        try:
                            cmd = command_queue.get(timeout=1.0)
                            if cmd == "CALL":
                                _internal_perform_call(page)
                            elif cmd == "EXIT":
                                context.close()
                                return
                            command_queue.task_done()
                        except queue.Empty:
                            pass
                        
                        if page.is_closed():
                            raise Exception("App Window closed unexpectedly")
                            
                    except Exception as e:
                        print(f"Worker Loop Error: {e}")
                        break

                context.close()
        
        except Exception as e:
            print(f"CRITICAL ERROR: {e}")
            print("Running Cleanup and Restarting...")
            force_cleanup_profile() 
            gui_queue.put(("status", "System Recovering..."))
            time.sleep(3)

def _handle_login_flow(page):
    gui_queue.put(("status", "Checking Login..."))
    
    try:
        page.goto("https://teams.microsoft.com", wait_until="domcontentloaded", timeout=60000)
    except: pass

    video_btn_selector = 'button[aria-label*="Video"], [data-tid="video-call-button"]'
    
    try:
        page.wait_for_selector(video_btn_selector, timeout=5000)
        print("Already Logged In.")
        return True
    except:
        pass

    print("First Run / Login Required.")
    gui_queue.put(("status", "Please Log In..."))
    switch_to_pwa() 
    
    try:
        page.click('button:has-text("Use the web app instead")', timeout=3000)
    except: pass

    print("Waiting for user to complete login...")
    for _ in range(150): 
        try:
            if page.locator(video_btn_selector).count() > 0:
                print("Login Detected! Completing setup...")
                return True
        except:
            pass
        time.sleep(2)
    
    print("Login timed out.")
    return False

def _internal_reset_ready(page):
    print("Resetting State...")
    gui_queue.put(("status", "Checking Line..."))
    switch_to_gui()

    video_btn = page.locator('button[aria-label*="Video"], [data-tid="video-call-button"]').first

    try:
        if video_btn.is_visible():
            print("FAST PATH: Button already visible.")
            gui_queue.put(("status", "Ready to Call"))
            gui_queue.put(("btn_state", "ready"))
            return
    except: pass

    print("Cleaning up potential popups...")
    try:
        page.locator('button[aria-label="Close"]').click(timeout=300)
    except: pass
    try:
        page.locator('button:has-text("Dismiss")').click(timeout=300)
    except: pass

    try:
        if video_btn.is_visible():
            print("READY after cleanup.")
            gui_queue.put(("status", "Ready to Call"))
            gui_queue.put(("btn_state", "ready"))
            return
    except: pass

    print("Button missing. (Direct navigation disabled)")
    time.sleep(2)

def _internal_perform_call(page):
    print("EXECUTE CALL...")
    gui_queue.put(("status", "Dialing..."))
    switch_to_pwa()

    try:
        page.locator('button[aria-label*="Video"], [data-tid="video-call-button"]').first.click()
    except Exception:
        try:
            page.keyboard.press("Alt+Shift+V")
        except Exception: pass

    call_active = False
    POLL_INTERVAL = 0.5
    for _ in range(120):
        try:
            join_btn = page.locator('button:has-text("Join now")')
            if join_btn.count() > 0 and join_btn.first.is_visible():
                join_btn.first.click()
                time.sleep(2)

            if (page.locator('button[title="Hang up"]').count() > 0 or 
                page.locator('[data-tid="call-duration"]').count() > 0):
                call_active = True
                break
            if page.get_by_text("Call ended", exact=False).count() > 0:
                break
        except Exception: pass
        time.sleep(POLL_INTERVAL)

    if call_active:
        print("Call Active.")
        gui_queue.put(("status", "Call in Progress"))
        while True:
            time.sleep(POLL_INTERVAL)
            try:
                if page.get_by_text("Call ended", exact=False).count() > 0: break
                if page.get_by_text("Quality of this call", exact=False).count() > 0: break
                if (page.locator('button[title="Hang up"]').count() == 0 and 
                    page.locator('[data-tid="call-duration"]').count() == 0):
                    time.sleep(1)
                    if page.locator('button[title="Hang up"]').count() == 0: break
            except: pass

    print("Call Finished.")
    switch_to_gui()
    gui_queue.put(("status", "Resetting..."))
    try:
        if page.get_by_text("Call ended", exact=False).count() > 0:
             page.locator('button[title="Dismiss"]').click(timeout=1000)
    except: pass
    try:
        page.locator('button[aria-label="Close"]').click(timeout=500)
    except: pass
    _internal_reset_ready(page)

# --- GUI CLASS ---

class HospibotGUI:
    """Three screens: a menu (Patrol Mode / Christmas Mode), the call screen, and the Christmas screen."""

    BG = "#0b132b"

    def __init__(self, root):
        self.root = root
        self.root.title("Hospibot Pi")
        self.root.configure(bg=self.BG)
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True)

        self.frame = tk.Frame(root, bg=self.BG)
        self.frame.pack(expand=True, fill="both")

        self.mode = None      # Tele's mode as reported by the mode manager
        self.screen = None    # "menu", "patrol" or "christmas"

        self._build_menu()
        self._build_patrol()
        self._build_christmas()

        # --- EXIT BUTTON RESTORED HERE ---
        self.exit_btn = tk.Button(root, text="EXIT", font=("Arial", 10), command=self.close_app, bg="red", fg="white")
        self.exit_btn.place(x=0, y=0)

        # Escape key kept as backup
        self.root.bind("<Escape>", lambda event: self.close_app())

        self.show_screen("menu")
        self.root.after(100, self.process_gui_queue)
        self.root.after(1000, self.poll_mode)
        self.root.after(50, self.animate_christmas)
        self.thread = threading.Thread(target=browser_worker, daemon=True)
        self.thread.start()

    # ------------------------------------------------------------------ screens
    def _build_menu(self):
        self.menu = tk.Frame(self.frame, bg=self.BG)
        box = tk.Frame(self.menu, bg=self.BG)
        box.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        tk.Button(
            box, text="Patrol Mode", font=("Helvetica", 44, "bold"), width=14,
            bg="#1f4068", activebackground="#2a5a94", fg="white", activeforeground="white",
            pady=60, relief="raised", bd=5, command=self.on_patrol_click
        ).pack(pady=40)
        tk.Button(
            box, text="Christmas Mode", font=("Helvetica", 44, "bold"), width=14,
            bg="#b3202a", activebackground="#d32f3a", fg="white", activeforeground="white",
            pady=60, relief="raised", bd=5, command=self.on_christmas_click
        ).pack(pady=40)

    def _build_patrol(self):
        self.patrol = tk.Frame(self.frame, bg=self.BG)
        self.inner_frame = tk.Frame(self.patrol, bg=self.BG)
        self.inner_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(self.inner_frame, text="Call for Assistance", font=("Helvetica", 50, "bold"), fg="white", bg=self.BG).pack(pady=60)

        self.status_var = tk.StringVar(value="Initializing Pi System...")
        self.lbl_status = tk.Label(self.inner_frame, textvariable=self.status_var, font=("Helvetica", 16), fg="#aaaaaa", bg=self.BG)

        self.btn = tk.Button(
            self.inner_frame, text="System Loading...", font=("Helvetica", 36, "bold"),
            bg="#333333", fg="white", padx=80, pady=40, relief="raised", bd=5,
            state="disabled", command=self.on_call_click
        )
        self.btn.pack(pady=20)
        self.lbl_status.pack(pady=20)

        tk.Button(
            self.patrol, text="Back", font=("Helvetica", 20, "bold"), bg="#333333", fg="white",
            padx=30, pady=12, command=lambda: self.show_screen("menu")
        ).place(relx=0.5, rely=0.93, anchor=tk.CENTER)

    def _build_christmas(self):
        self.christmas = tk.Frame(self.frame, bg="#0a1a3a")
        self.canvas = tk.Canvas(self.christmas, bg="#0a1a3a", highlightthickness=0)
        self.canvas.pack(expand=True, fill="both")
        self.snow = []
        self.lights = []
        self.scene_size = None
        self.anim_step = 0
        tk.Button(
            self.christmas, text="Stop", font=("Helvetica", 24, "bold"), bg="#b3202a", fg="white",
            activebackground="#d32f3a", activeforeground="white", padx=50, pady=16,
            command=self.on_stop_christmas_click
        ).place(relx=0.5, rely=0.93, anchor=tk.CENTER)

    def show_screen(self, name):
        if name == self.screen:
            return
        self.screen = name
        for key, frame in (("menu", self.menu), ("patrol", self.patrol), ("christmas", self.christmas)):
            if key == name:
                frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            else:
                frame.place_forget()
        self.exit_btn.lift() if hasattr(self, "exit_btn") else None

    # ------------------------------------------------------------------ Christmas drawing
    def draw_scene(self, w, h):
        """Draws the still parts once: ground, tree, Santa and the greeting."""
        c = self.canvas
        c.delete("all")
        self.snow, self.lights = [], []
        c.create_oval(-w * 0.3, h * 0.80, w * 1.3, h * 1.25, fill="#eef4ff", outline="")  # Snowy ground

        # Tree
        tx, base = w * 0.5, h * 0.80
        c.create_rectangle(tx - w * 0.035, base - h * 0.05, tx + w * 0.035, base, fill="#6b4226", outline="")
        for width, top, bottom in ((0.20, 0.44, 0.56), (0.27, 0.51, 0.66), (0.34, 0.60, 0.76)):
            c.create_polygon(tx, h * top, tx - w * width, h * bottom, tx + w * width, h * bottom,
                             fill="#1e7a3c", outline="#145c2b", width=3)
        c.create_text(tx, h * 0.435, text="★", font=("Helvetica", int(w * 0.09)), fill="#ffd54a")
        for i in range(18):
            level = random.choice(((0.50, 0.55, 0.13), (0.56, 0.65, 0.19), (0.65, 0.75, 0.26)))
            y = random.uniform(level[0], level[1])
            spread = level[2] * (y - level[0] + 0.03) / (level[1] - level[0] + 0.03)
            x = tx + random.uniform(-spread, spread) * w
            r = w * 0.014
            self.lights.append(c.create_oval(x - r, y * h - r, x + r, y * h + r, fill="#ffd54a", outline=""))

        # Santa's face
        sx, sy, r = w * 0.5, h * 0.24, w * 0.14
        c.create_oval(sx - r * 1.25, sy - r * 0.1, sx + r * 1.25, sy + r * 2.0, fill="white", outline="")       # Beard
        c.create_oval(sx - r, sy - r, sx + r, sy + r, fill="#f6c9a8", outline="")                              # Face
        c.create_polygon(sx - r * 1.05, sy - r * 0.55, sx + r * 1.05, sy - r * 0.55, sx + r * 0.75, sy - r * 2.1,
                         fill="#c62828", outline="", smooth=False)                                          # Hat
        c.create_oval(sx + r * 0.55, sy - r * 2.3, sx + r * 0.95, sy - r * 1.9, fill="white", outline="")     # Pompom
        c.create_rectangle(sx - r * 1.15, sy - r * 0.75, sx + r * 1.15, sy - r * 0.40, fill="white", outline="")  # Brim
        for dx in (-0.38, 0.38):
            c.create_oval(sx + r * dx - r * 0.09, sy - r * 0.15, sx + r * dx + r * 0.09, sy + r * 0.03, fill="#222", outline="")
        c.create_oval(sx - r * 0.16, sy + r * 0.10, sx + r * 0.16, sy + r * 0.42, fill="#e57373", outline="")  # Nose
        c.create_oval(sx - r * 0.75, sy + r * 0.35, sx + r * 0.02, sy + r * 0.80, fill="white", outline="")    # Moustache
        c.create_oval(sx - r * 0.02, sy + r * 0.35, sx + r * 0.75, sy + r * 0.80, fill="white", outline="")

        c.create_text(w * 0.5, h * 0.045, text="Merry Christmas!", font=("Helvetica", int(w * 0.085), "bold"), fill="#ffd54a")

        for i in range(70):
            x, y, r = random.uniform(0, w), random.uniform(0, h), random.uniform(2, 5)
            flake = c.create_oval(x - r, y - r, x + r, y + r, fill="white", outline="")
            self.snow.append([flake, random.uniform(1.5, 4.5), random.uniform(-0.6, 0.6)])

    def animate_christmas(self):
        if self.screen == "christmas":
            w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
            if w > 50 and (w, h) != self.scene_size:
                self.scene_size = (w, h)
                self.draw_scene(w, h)
            self.anim_step += 1
            for flake in self.snow:
                self.canvas.move(flake[0], flake[2], flake[1])
                x0, y0, x1, y1 = self.canvas.coords(flake[0])
                if y0 > h:
                    self.canvas.move(flake[0], random.uniform(0, w) - x0, -y1)
            if self.anim_step % 8 == 0:
                for light in self.lights:
                    self.canvas.itemconfig(light, fill=random.choice(("#ffd54a", "#ff5252", "#ffffff", "#40c4ff")))
        self.root.after(50, self.animate_christmas)

    # ------------------------------------------------------------------ mode handling
    def poll_mode(self):
        """Ask the mode manager for the current mode, without blocking the screen."""
        def work():
            try:
                mode = requests.get(MODE_URL, timeout=1.5).json().get("mode")
            except Exception:
                mode = None
            self.root.after(0, lambda: self.mode_reported(mode))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(2000, self.poll_mode)

    def mode_reported(self, mode):
        """Keep the screen in step with Tele, also when the mode was changed from elsewhere."""
        self.mode = mode
        if mode == "CHRISTMAS" and self.screen != "christmas":
            self.show_screen("christmas")
        elif mode is not None and mode != "CHRISTMAS" and self.screen == "christmas":
            self.show_screen("menu")

    def set_mode(self, mode):
        def work():
            try:
                new_mode = requests.post(f"{MODE_URL}/{mode}", timeout=2).json().get("mode")
            except Exception:
                new_mode = None
            if new_mode is not None:
                self.root.after(0, lambda: setattr(self, "mode", new_mode))
        threading.Thread(target=work, daemon=True).start()

    def on_patrol_click(self):
        self.set_mode("PATROL")
        self.show_screen("patrol")

    def on_christmas_click(self):
        self.mode = "CHRISTMAS"
        self.set_mode("CHRISTMAS")
        self.show_screen("christmas")

    def on_stop_christmas_click(self):
        self.mode = "PATROL"
        self.set_mode("PATROL")
        self.show_screen("menu")

    # ------------------------------------------------------------------ call handling
    def process_gui_queue(self):
        try:
            while True:
                msg_type, msg_data = gui_queue.get_nowait()
                if msg_type == "status": self.status_var.set(msg_data)
                elif msg_type == "btn_state":
                    if msg_data == "ready": self.btn.config(state="normal", bg="#1f4068", text="Start Video Call")
                    elif msg_data == "disabled": self.btn.config(state="disabled", bg="#333333", text="Please Wait...")
                elif msg_type == "set_view":
                    if msg_data == "gui":
                        self.root.deiconify(); self.root.attributes('-fullscreen', True); self.root.attributes('-topmost', True); self.root.lift(); self.root.focus_force()
                    elif msg_data == "browser": self.root.withdraw()
                elif msg_type == "error": messagebox.showerror("Error", msg_data)
                gui_queue.task_done()
        except queue.Empty: pass
        self.root.after(100, self.process_gui_queue)

    def on_call_click(self):
        self.btn.config(state="disabled", text="Calling...")
        self.status_var.set("Connecting...")
        command_queue.put("CALL")

    def close_app(self):
        command_queue.put("EXIT")
        self.root.destroy()

if __name__ == "__main__":
    import traceback
    log_path = os.path.expanduser("~/hospibot_error.log")
    try:
        root = tk.Tk()
        app = HospibotGUI(root)
        root.mainloop()
    except Exception:
        with open(log_path, "w") as f:
            traceback.print_exc(file=f)
        raise
