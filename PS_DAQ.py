import owl
import sys
import atexit
import time
import re
import struct

num_markers = 28 
PERSIST = True
OWL = owl.Context()
#OWL.debug = True

def shutdown():
    global PERSIST
    global OWL

    print("#stopping")

    PERSIST = False
    OWL.done()
    OWL.close()

atexit.register(shutdown)

def parse_channels(options):
    ch = re.findall("channelids=([0-9,]+)", options)[0].split(',')
    return [int(x,10) for x in filter(lambda x: len(x) > 0, ch)]

def wait_for_channel_info():
    t0 = time.time()
    while OWL.streaming() and PERSIST:
        if time.time() - t0 > 30:
            raise Exception("timed out waiting for device info")
        e = OWL.nextEvent()
        if e == None: continue
        for di in OWL.property("deviceinfo"):
            # print(di.name)
            if di.name == "daq":
                print("#", e)
                print("#", di)
                return parse_channels(di.options)

print("# open: ", OWL.open(sys.argv[1]))
print("# init: ", OWL.initialize("streaming=1 event.inputs=1 profile=LowerBodyProfile"))

channels = wait_for_channel_info()
print("# channels:", channels)

st = struct.Struct("<%dh" % (len(channels)))
frame = 0
frame_flag = 0

# PhaseSpace output
file_mocap = open("markers.txt", "w")
file_mocap.write("Time" "\t")
for i in range(num_markers):
    file_mocap.write("X" "%d" "\t" "Y" "%d" "\t" "Z" "%d" "\t" % (i, i, i))
file_mocap.write("\n")

# DAQ output and headers
file_daq = open("analog.txt", "w")
file_daq.write("Time" "\t" "FrameFlag" "\t")
file_daq.write("EMG1" "\t" "EMG2" "\t" "EMG3" "\t" "EMG4" "\t" "EMG5" "\t" "EMG6" "\t" "EMG7" "\t" "EMG8" "\t")
file_daq.write("R.FX" "\t" "R.FY" "\t" "R.FZ" "\t" "R.MX" "\t" "R.MY" "\t" "R.MZ" "\t")
file_daq.write("L.FX" "\t" "L.FY" "\t" "L.FZ" "\t" "L.MX" "\t" "L.MY" "\t" "L.MZ" "\t")
file_daq.write("TTL" "\n")

# Scaling factor: -10V ~ 10V
scale_factor = 10/32767

# Get data
while OWL.streaming() and PERSIST:
    evt = OWL.nextEvent()
    if evt == None: continue

    # Get marker data
    if evt.type_id == owl.Type.FRAME:
        # print markers
        if "markers" in evt:
            for m in evt.markers:
                if m.id == 0:
                    file_mocap.write("%8d" "\t" "%8d" "\t" "%8d" "\t" "%8d" "\t" % (m.time, m.x, m.y, m.z)) 
                elif m.id == num_markers-1:
                    file_mocap.write("%8d" "\t" "%8d" "\t" "%8d" "\n" % (m.x, m.y, m.z))
                else:
                    file_mocap.write("%8d" "\t" "%8d" "\t" "%8d" "\t" % (m.x, m.y, m.z)) 
                #print(m)

    # Get analog data
    if evt.type_id == owl.Type.INPUT:
        if frame != evt.time:
            frame = evt.time
            frame_flag = (frame_flag + 1) % 2

        for inp in evt.data:
            for offset in range(0, len(inp.data), len(channels)*2):
                analog = st.unpack_from(inp.data, offset)
                file_daq.write("%8d""\t""%2d""\t" % (inp.time, frame_flag))
                for i in range(0,len(analog)-1):
                    scaled_out = analog[i]*scale_factor
                    file_daq.write("%.5f" "\t" % scaled_out)
                    #sys.stdout.write("%.5f " % scaled_out)
                    #sys.stdout.write("%8d " % analog[i])

                TTL = analog[len(analog)-1]*scale_factor
                file_daq.write("%.5f" "\n" % TTL)
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

OWL.shutdown()