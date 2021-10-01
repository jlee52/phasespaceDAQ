import owl
import sys
import atexit
# import time
import re
import struct

import datetime
import queue
import logging
import signal
import time
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from tkinter import ttk, VERTICAL, HORIZONTAL, N, S, E, W

# PhaseSpaceDAQ info
__version__ = '1.0'
__ip__ = '192.168.1.230'
n_markers = 36

logger = logging.getLogger(__name__)


class Clock(threading.Thread):
    """Class to display the time every seconds
    Every 5 seconds, the time is displayed using the logging.ERROR level
    to show that different colors are associated to the log levels
    """

    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()

    def run(self):
        logger.debug('Clock started')
        previous = -1
        while not self._stop_event.is_set():
            now = datetime.datetime.now()
            if previous != now.second:
                previous = now.second
                if now.second % 5 == 0:
                    level = logging.ERROR
                else:
                    level = logging.INFO
                logger.log(level, now)
            time.sleep(0.2)

    def stop(self):
        self._stop_event.set()


class QueueHandler(logging.Handler):
    """Class to send logging records to a queue
    It can be used from different threads
    The ConsoleUi class polls this queue to display records in a ScrolledText widget
    """
    # Example from Moshe Kaplan: https://gist.github.com/moshekaplan/c425f861de7bbf28ef06
    # (https://stackoverflow.com/questions/13318742/python-logging-to-tkinter-text-widget) is not thread safe!
    # See https://stackoverflow.com/questions/43909849/tkinter-python-crashes-on-new-thread-trying-to-log-on-main-thread

    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(record)


class DAQ():

    def __init__(self):

        self.OWL = owl.Context()
        self.PERSIST = True
        atexit.register(self.shutdown)
        self.ip = __ip__
        self.n_markers = n_markers
        self.frame = 0
        self.frame_flag = 0
        self.mocap = []
        self.ai = []
        self.channels = []
        self.st = []
        # Scaling factor to make signal ragne -10V ~ 10V
        self.scale_factor = 10/32767

    def shutdown(self):
        # global PERSIST
        # global OWL

        print("#stopping")

        self.PERSIST = False
        self.mocap.close()
        self.ai.close()
        self.OWL.done()
        self.OWL.close()

    def parse_channels(self, options):
        ch = re.findall("channelids=([0-9,]+)", options)[0].split(',')
        return [int(x,10) for x in filter(lambda x: len(x) > 0, ch)]

    def wait_for_channel_info(self):
        t0 = time.time()
        while self.OWL.streaming() and self.PERSIST:
            if time.time() - t0 > 30:
                raise Exception("timed out waiting for device info")
            e = self.OWL.nextEvent()
            if e == None: continue
            for di in self.OWL.property("deviceinfo"):
                # print(di.name)
                if di.name == "daq":
                    print("#", e)
                    print("#", di)
                    lvl = logging.INFO
                    logger.log(lvl, "\n #" + str(e))
                    logger.log(lvl, "\n #" + str(di))
                    return self.parse_channels(di.options)        

    def initialize(self):
        global fname_mocap
        global fname_ai

        print(fname_mocap)
        print(fname_ai)

        msg_open = self.OWL.open(self.ip)
        msg_init = self.OWL.initialize("streaming=1 event.inputs=1 profile=LowerBodyProfile")
        print("# open: ", msg_open)
        print("# init: ", msg_init)

        lvl = logging.INFO
        logger.log(lvl, " Initialize...")
        logger.log(lvl, " # open: " +  str(msg_open))
        logger.log(lvl, " # init: " +  str(msg_init))
        logger.log(lvl, " " + fname_mocap + " created")    
        logger.log(lvl, " " + fname_ai + " created")

        self.channels = self.wait_for_channel_info()
        print("# channels:", self.channels)
        self.st = struct.Struct("<%dh" % (len(self.channels)))

        # PhaseSpace data write 
        self.mocap = open(fname_mocap, "w")
        self.mocap.write("Time" "\t")
        for i in range(self.n_markers):
            self.mocap.write("X" "%d" "\t" "Y" "%d" "\t" "Z" "%d" "\t" % (i, i, i))
        self.mocap.write("\n")

        # Analog data write 
        self.ai = open(fname_ai, "w")
        self.ai.write("Time" "\t" "FrameFlag" "\t")
        self.ai.write("EMG1" "\t" "EMG2" "\t" "EMG3" "\t" "EMG4" "\t" "EMG5" "\t" "EMG6" "\t" "EMG7" "\t" "EMG8" "\t")
        self.ai.write("R.FX" "\t" "R.FY" "\t" "R.FZ" "\t" "R.MX" "\t" "R.MY" "\t" "R.MZ" "\t")
        self.ai.write("L.FX" "\t" "L.FY" "\t" "L.FZ" "\t" "L.MX" "\t" "L.MY" "\t" "L.MZ" "\t")
        self.ai.write("TTL" "\n") 

        return self.channels

    def record(self):
        while self.OWL.streaming() and self.PERSIST:
            evt = self.OWL.nextEvent()
            if evt == None: continue

            # Get marker data
            if evt.type_id == owl.Type.FRAME:
                # print markers
                if "markers" in evt:
                    for m in evt.markers:
                        if m.id == 0:
                            self.mocap.write("%8d" "\t" "%.5f" "\t" "%.5f" "\t" "%.5f" "\t" % (m.time, m.x, m.y, m.z)) 
                        elif m.id == self.n_markers-1:
                            self.mocap.write("%.5f" "\t" "%.5f" "\t" "%.5f" "\n" % (m.x, m.y, m.z))
                        else:
                            self.mocap.write("%.5f" "\t" "%.5f" "\t" "%.5f" "\t" % (m.x, m.y, m.z)) 
                        #print(m)

            # Get analog data
            if evt.type_id == owl.Type.INPUT:
                if self.frame != evt.time:
                    self.frame = evt.time
                    self.frame_flag = (self.frame_flag + 1) % 2

                for inp in evt.data:
                    for offset in range(0, len(inp.data), len(self.channels)*2):
                        analog = self.st.unpack_from(inp.data, offset)
                        self.ai.write("%8d""\t""%2d""\t" % (inp.time, self.frame_flag))
                        for i in range(0,len(analog)-1):
                            scaled_out = analog[i]*self.scale_factor
                            self.ai.write("%.5f" "\t" % scaled_out)
                            #sys.stdout.write("%.5f " % scaled_out)
                            #sys.stdout.write("%8d " % analog[i])

                        TTL = analog[len(analog)-1]*self.scale_factor
                        self.ai.write("%.5f" "\n" % TTL)
                        #sys.stdout.write("\n")

            elif evt.type_id == owl.Type.ERROR:
                # handle errors
                print(evt.name, evt.data)
                if evt.name == "fatal":
                    break
            elif evt.name == "done":
                # done event is sent when master connection stops session
                print("done")
                break


def threading_record():
    global daq

    daq = DAQ()
    daq.initialize()
    daq.record()


class ConsoleUi:
    """Poll messages from a logging queue and display them in a scrolled text widget"""

    def __init__(self, frame):
        self.frame = frame
        # Create a ScrolledText wdiget
        self.scrolled_text = ScrolledText(frame, state='disabled', height=12)
        self.scrolled_text.grid(row=0, column=0, sticky=(N, S, W, E))
        self.scrolled_text.configure(font='TkFixedFont')
        self.scrolled_text.tag_config('INFO', foreground='black')
        self.scrolled_text.tag_config('DEBUG', foreground='gray')
        self.scrolled_text.tag_config('WARNING', foreground='orange')
        self.scrolled_text.tag_config('ERROR', foreground='red')
        self.scrolled_text.tag_config('CRITICAL', foreground='red', underline=1)
        # Create a logging handler using a queue
        self.log_queue = queue.Queue()
        self.queue_handler = QueueHandler(self.log_queue)
        formatter = logging.Formatter('%(asctime)s: %(message)s')
        self.queue_handler.setFormatter(formatter)
        logger.addHandler(self.queue_handler)
        # Start polling messages from the queue
        self.frame.after(100, self.poll_log_queue)

    def display(self, record):
        msg = self.queue_handler.format(record)
        self.scrolled_text.configure(state='normal')
        self.scrolled_text.insert(tk.END, msg + '\n', record.levelname)
        self.scrolled_text.configure(state='disabled')
        # Autoscroll to the bottom
        self.scrolled_text.yview(tk.END)

    def poll_log_queue(self):
        # Check every 100ms if there is a new message in the queue to display
        while True:
            try:
                record = self.log_queue.get(block=False)
            except queue.Empty:
                break
            else:
                self.display(record)
        self.frame.after(100, self.poll_log_queue)


class FormUi:

    def __init__(self, frame):
        self.frame = frame
        # self.daq = DAQ()
        
        # Create text box 
        self.subject = tk.StringVar()
        ttk.Label(self.frame, text='Subject:').grid(column=0, row=0, sticky=W)
        ttk.Entry(self.frame, textvariable=self.subject, width=25).grid(column=1, row=0, sticky=(W, E), pady=5)
        self.condition = tk.StringVar()
        ttk.Label(self.frame, text='Condition:').grid(column=0, row=1, sticky=W)
        ttk.Entry(self.frame, textvariable=self.condition, width=25).grid(column=1, row=1, sticky=(W, E), pady=5)

        # Add a button to log the message
        self.btn_record = ttk.Button(self.frame, text='Start Recording', command=self.start_record)
        self.btn_record.grid(column=0, row=4, sticky=(W, E), columnspan=2, pady=10)
        self.btn_stop = ttk.Button(self.frame, text='Stop Recording', command=self.stop_record)
        self.btn_stop.grid(column=0, row=5, sticky=(W, E), columnspan=2, pady=10)

    def start_record(self):
        global fname_mocap
        global fname_ai

        values = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        lvl = getattr(logging, values[1])
        
        fname_mocap = self.subject.get() + "_" + self.condition.get() + "_markers.txt"
        fname_ai = self.subject.get() + "_" + self.condition.get() + "_analog.txt"
        logger.log(lvl, " " + fname_mocap + " created")    
        logger.log(lvl, " " + fname_ai + " created") 

        thread = threading.Thread(target=threading_record)
        thread.start()  

        logger.log(lvl, " Start recording...")
    
    def stop_record(self):
        global daq

        values = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        lvl = getattr(logging, values[1])
        msg = " Stop recording..."
        logger.log(lvl, msg)

        daq.shutdown()
        # sys.exit()

        

class ThirdUi:

    def __init__(self, frame):
        self.frame = frame
        # copyright_symb = u"\u0049"
        copyright_symb = u"\N{COPYRIGHT SIGN}"
        ttk.Label(self.frame, text= "PhaseSpaceDAQ", font=('bold')).grid(column=0, row=1, sticky=W)
        ttk.Label(self.frame, text= " This software is for collecting").grid(column=0, row=2, sticky=W)
        ttk.Label(self.frame, text= "  - PhaseSpace marker data").grid(column=0, row=3, sticky=W)
        ttk.Label(self.frame, text= "  - Analog signals (e.g., GRF and EMG)\n").grid(column=0, row=4, sticky=W) 
        ttk.Label(self.frame, text= copyright_symb + " 2021 Rewire Lab., Jeonghwan Lee (jlee85@utexas.edu)").grid(column=0, row=5, sticky=W)
        

class App:

    def __init__(self, root):
        self.root = root
        root.title("PhaseSpaceDAQ " + "v" + __version__)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        # Create the panes and frames
        vertical_pane = ttk.PanedWindow(self.root, orient=VERTICAL)
        vertical_pane.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        horizontal_pane = ttk.PanedWindow(vertical_pane, orient=HORIZONTAL)
        vertical_pane.add(horizontal_pane)
        form_frame = ttk.Labelframe(horizontal_pane, text="Trial")
        form_frame.columnconfigure(1, weight=1)
        horizontal_pane.add(form_frame, weight=1)
        console_frame = ttk.Labelframe(horizontal_pane, text="Log")
        console_frame.columnconfigure(0, weight=1)
        console_frame.rowconfigure(0, weight=1)
        horizontal_pane.add(console_frame, weight=1)
        third_frame = ttk.Labelframe(vertical_pane, text="NOTE")
        vertical_pane.add(third_frame, weight=1)

        # Initialize all frames
        self.form = FormUi(form_frame)
        self.console = ConsoleUi(console_frame)
        self.third = ThirdUi(third_frame) 
        # self.clock = Clock()
        # self.clock.start()
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
        self.root.bind('<Control-q>', self.quit)
        signal.signal(signal.SIGINT, self.quit)

    def quit(self, *args):
        # self.clock.stop()
        self.root.destroy()



def main():
    logging.basicConfig(level=logging.DEBUG)
    root = tk.Tk()
    app = App(root)
    app.root.mainloop()


if __name__ == '__main__':
    main()
