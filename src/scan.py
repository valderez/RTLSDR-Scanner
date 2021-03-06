#
# rtlsdr_scan
#
# http://eartoearoak.com/software/rtlsdr-scanner
#
# Copyright 2012 - 2014 Al Brown
#
# A frequency scanning GUI for the OsmoSDR rtl-sdr library at
# http://sdr.osmocom.org/trac/wiki/rtl-sdr
#
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import itertools
import math
import threading
import time

import matplotlib
import rtlsdr

from constants import SAMPLE_RATE, BANDWIDTH, WINFUNC
from events import EventThread, Event, post_event
import rtltcp


class ThreadScan(threading.Thread):
    def __init__(self, notify, sdr, settings, device, samples, isCal):
        threading.Thread.__init__(self)
        self.name = 'Scan'
        self.notify = notify
        self.sdr = sdr
        self.fstart = settings.start * 1e6
        self.fstop = settings.stop * 1e6
        self.samples = int(samples)
        self.isCal = isCal
        self.indexRtl = settings.indexRtl
        self.isDevice = settings.devicesRtl[device].isDevice
        self.server = settings.devicesRtl[device].server
        self.port = settings.devicesRtl[device].port
        self.gain = settings.devicesRtl[device].gain
        self.lo = settings.devicesRtl[device].lo * 1e6
        self.offset = settings.devicesRtl[device].offset
        self.cancel = False

        post_event(self.notify, EventThread(Event.STARTING))
        steps = int((self.__f_stop() - self.__f_start()) / self.__f_step())
        post_event(self.notify, EventThread(Event.STEPS, steps))
        self.start()

    def __f_start(self):
        return self.fstart - self.offset - BANDWIDTH

    def __f_stop(self):
        return self.fstop + self.offset + BANDWIDTH * 2

    def __f_step(self):
        return BANDWIDTH / 2

    def __rtl_setup(self):

        if self.sdr is not None:
            return

        tuner = 0

        if self.isDevice:
            try:
                self.sdr = rtlsdr.RtlSdr(self.indexRtl)
                self.sdr.set_sample_rate(SAMPLE_RATE)
                self.sdr.set_manual_gain_enabled(1)
                self.sdr.set_gain(self.gain)
                tuner = self.sdr.get_tuner_type()
            except IOError as error:
                post_event(self.notify, EventThread(Event.ERROR,
                                                          0, error.message))
        else:
            try:
                self.sdr = rtltcp.RtlTcp(self.server, self.port)
                self.sdr.set_sample_rate(SAMPLE_RATE)
                self.sdr.set_manual_gain_enabled(1)
                self.sdr.set_gain(self.gain)
                tuner = self.sdr.get_tuner_type()
            except IOError as error:
                post_event(self.notify, EventThread(Event.ERROR,
                                                          0, error))

        return tuner

    def run(self):
        tuner = self.__rtl_setup()
        if self.sdr is None:
            return
        post_event(self.notify, EventThread(Event.INFO, None, tuner))

        freq = self.__f_start()
        timeStamp = math.floor(time.time())
        while freq <= self.__f_stop():
            if self.cancel:
                post_event(self.notify,
                           EventThread(Event.STOPPED))
                self.rtl_close()
                return
            try:
                scan = self.rtl_scan(freq)
                post_event(self.notify,
                           EventThread(Event.DATA, freq,
                                            (timeStamp, scan)))
            except IOError:
                if self.sdr is not None:
                    self.rtl_close()
                self.__rtl_setup()
            except (TypeError, AttributeError) as error:
                if self.notify:
                    post_event(self.notify,
                               EventThread(Event.ERROR,
                                                 0, error.message))
                return
            except WindowsError:
                if self.sdr is not None:
                    self.rtl_close()

            freq += self.__f_step()

        post_event(self.notify, EventThread(Event.FINISHED, 0, None))

        if self.isCal:
            post_event(self.notify, EventThread(Event.CAL))

    def abort(self):
        self.cancel = True

    def rtl_scan(self, freq):
        self.sdr.set_center_freq(freq + self.lo)
        capture = self.sdr.read_samples(self.samples)

        return capture

    def rtl_close(self):
        self.sdr.close()

    def get_sdr(self):
        return self.sdr


def anaylse_data(freq, data, cal, nfft, overlap, winFunc):
    spectrum = {}
    timeStamp = data[0]
    samples = data[1]
    pos = WINFUNC[::2].index(winFunc)
    function = WINFUNC[1::2][pos]
    powers, freqs = matplotlib.mlab.psd(samples,
                                        NFFT=nfft,
                                        noverlap=int((nfft) * overlap),
                                        Fs=SAMPLE_RATE / 1e6,
                                        window=function(nfft))
    for freqPsd, pwr in itertools.izip(freqs, powers):
        xr = freqPsd + (freq / 1e6)
        xr = xr + (xr * cal / 1e6)
        spectrum[xr] = pwr

    return (timeStamp, freq, spectrum)


def update_spectrum(notify, lock, start, stop, freqCentre, data, offset,
                    spectrum, average, alertLevel=None):
    with lock:
        updated = False
        if average:
            if len(spectrum) > 0:
                timeStamp = min(spectrum)
            else:
                timeStamp = data[0]
        else:
            timeStamp = data[0]
        scan = data[1]

        upperStart = freqCentre + offset
        upperEnd = freqCentre + offset + BANDWIDTH / 2
        lowerStart = freqCentre - offset - BANDWIDTH / 2
        lowerEnd = freqCentre - offset

        if not timeStamp in spectrum:
            spectrum[timeStamp] = {}

        for freq in scan:
            if start <= freq < stop:
                power = 10 * math.log10(scan[freq])
                if upperStart <= freq * 1e6 <= upperEnd or \
                   lowerStart <= freq * 1e6 <= lowerEnd:
                    if freq in spectrum[timeStamp]:
                        spectrum[timeStamp][freq] = \
                            (spectrum[timeStamp][freq] + power) / 2
                        if alertLevel is not None and \
                        spectrum[timeStamp][freq] > alertLevel:
                            post_event(notify, EventThread(Event.LEVEL))
                        updated = True
                    else:
                        spectrum[timeStamp][freq] = power
                        updated = True

    post_event(notify, EventThread(Event.UPDATED, None, updated))


if __name__ == '__main__':
    print 'Please run rtlsdr_scan.py'
    exit(1)
