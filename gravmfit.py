from astropy.io import fits
from astropy.convolution import Gaussian2DKernel
import matplotlib.pyplot as plt
from matplotlib import gridspec
import numpy as np
import scipy as sp
import emcee
import corner
from multiprocessing import Pool
from fpdf import FPDF
from PIL import Image
from scipy import signal, optimize, interpolate
import math
import mpmath
from pkg_resources import resource_filename
from numba import njit, prange
from datetime import timedelta, datetime
import multiprocessing
import sys
import os 

from .gravdata import *

try:
    from generalFunctions import *
    set_style('show')
except (NameError, ModuleNotFoundError):
    pass

color1 = '#C02F1D'
color2 = '#348ABD'
color3 = '#F26D21'
color4 = '#7A68A6'

    
def mathfunc_real(values, dt):
    return np.trapz(np.real(values), dx=dt, axis=0)
   

def mathfunc_imag(values, dt):
    return np.trapz(np.imag(values), dx=dt, axis=0)


def complex_quadrature_num(func, a, b, theta, nsteps=int(1e2)):
    t = np.logspace(np.log10(a), np.log10(b), nsteps)
    dt = np.diff(t, axis=0)
    values = func(t, *theta)
    real_integral = mathfunc_real(values, dt)
    imag_integral = mathfunc_imag(values, dt)
    return real_integral + 1j*imag_integral


def procrustes(a,target,padval=0):
    try:
        if len(target) != a.ndim:
            raise TypeError('Target shape must have the same number of dimensions as the input')
    except TypeError:
        raise TypeError('Target must be array-like')

    try:
        #Get array in the right size to use
        b = np.ones(target,a.dtype)*padval
    except TypeError:
        raise TypeError('Pad value must be numeric')
    except ValueError:
        raise ValueError('Pad value must be scalar')

    aind = [slice(None,None)]*a.ndim
    bind = [slice(None,None)]*a.ndim

    for dd in range(a.ndim):
        if a.shape[dd] > target[dd]:
            diff = (a.shape[dd]-target[dd])/2.
            aind[dd] = slice(int(np.floor(diff)),int(a.shape[dd]-np.ceil(diff)))
        elif a.shape[dd] < target[dd]:
            diff = (target[dd]-a.shape[dd])/2.
            bind[dd] = slice(int(np.floor(diff)),int(target[dd]-np.ceil(diff)))
            
    b[bind] = a[aind]
    return b



class GravPhaseMaps():
    def createPhasemaps(self, nthreads=1, smooth=10, plot=True, datayear=2019):
        if datayear == 2019:
            zerfile='phasemap_zernike_20200918_diff_2019data.npy'
        elif datayear == 2020:
            zerfile='phasemap_zernike_20200922_diff_2020data.npy'
        else:
            raise ValueError('Datayear has to be 2019 or 2020')
        print('Used file: %s' % zerfile)
        
        def phase_screen(A00, A1m1, A1p1, A2m2, A2p2, A20, A3m1, A3p1, A3m3, 
                         A3p3, A4m2, A4p2, A4m4, A4p4, A40,  A5m1, A5p1, A5m3, 
                         A5p3, A5m5, A5p5, A6m6, A6p6, A6m4, A6p4, A6m2, A6p2,
                         A60, B1m1, B1p1, B20, B2m2, B2p2,
                         lam0=2.2, MFR=0.6308, stopB=8.0, stopS=0.96, 
                         d1=8.0, dalpha=1., totN=1024, amax=100):
            
            """
            Simulate phase screens taking into account static aberrations.
            
            Parameters:
            -----------
            * Static aberrations in the pupil plane are described by low-order Zernicke polynomials
            * Their amplitudes are in units of micro-meter

            00: A00  (float) : piston
            01: A1m1 (float) : vertical tilt
            02: A1p1 (float) : horizontal tilt
            03: A2m2 (float) : vertical astigmatism
            04: A2p2 (float) : horizontal astigmatism
            05: A20  (float) : defocuss
            06: A3m1 (float) : vertical coma
            07: A3p1 (float) : horizontal coma
            08: A3m3 (float) : vertical trefoil
            09: A3p3 (float) : oblique trefoil
            10: A4m2 (float) : oblique secondary astigmatism
            11: A4p2 (float) : vertical secondary astigmatism
            12: A4m4 (float) : oblique quadrafoil
            13: A4p4 (float) : vertical quadrafoil
            14: A40  (float) : primary spherical
            15: A5m1
            16: A5p1
            17: A5m3
            18: A5p3
            19: A5m5
            20: A5p5
            21: A6m6
            22: A6p6
            23: A6m4
            24: A6p4
            25: A6m2
            26: A6p2
            27: A60

            * Static aberrations in the focal plane
            B1m1 (float) : missplacement of the fiber mode in u1-direction in 
                           meters / Zernike coefficient if coefficients > B20 != 0
            B1p1 (float) : missplacement of the fiber mode in u2-direction in 
                           meters / Zernike coefficient if coefficients > B20 != 0
            B20  (float) : defocuss
            B2m2 (float) : vertical astigmatism
            B2p2 (float) : horizontal astigmatism

            * optical system
            MFR (float)   : sigma of the fiber mode profile in units of dish radius
            stopB (float) : outer stop diameter in meters
            stopS (float) : inner stop diameter in meters

            * further parameters specify the output grid
            dalpha (float) : pixel width in the imaging plane in mas
            totN   (float) : total number of pixels in the pupil plane
            lam0   (float) : wavelength at which the phase screen is computed in 
                             micro-meter
            d1     (float) : telescope to normalize Zernike RMS in m (UT=8.0, AT=1.82)
            amax   (float) : maximum off-axis distance in the maps returned
            
            """ 
            
            #--- coordinate scaling ---#
            lam0   = lam0*1e-6
            mas    = 1.e-3 * (2.*np.pi/360) *1./3600
            ext    = totN*d1/lam0*mas*dalpha*dalpha
            du     = dalpha/ext*d1/lam0
                
            #--- coordinates ---#
            ii     = np.arange(totN) - (totN/2)
            ii     = np.fft.fftshift(ii)
            
            # image plane
            a1, a2 = np.meshgrid(ii*dalpha, ii*dalpha)
            aa     = np.sqrt(a1*a1 + a2*a2)
            
            # pupil plane
            u1, u2 = np.meshgrid(ii*du*lam0, ii*du*lam0)
            r     = np.sqrt( u1*u1 + u2*u2 )
            t      = np.angle(u1 + 1j*u2)

            #--- cut our central part ---#
            hmapN = int(amax/dalpha)
            cc = slice(int(totN/2)-hmapN, int(totN/2)+hmapN+1)
            if 2*hmapN > totN:
                print('Requested map sizes too large')
                return False

            #--- pupil function ---#
            pupil = r<(stopB/2.)
            if stopS > 0.:
                pupil = np.logical_and( r<(stopB/2.), r>(stopS/2.) )
            
            #--- fiber profile ---#
            fiber = np.exp(-0.5*(r/(MFR*d1/2.))**2)
            if B1m1!=0 or B1p1!=0:
                fiber = np.exp(-0.5*((u1-B1m1)**2 + (u2-B1p1)**2)/(MFR*d1/2.)**2)

            # for higher-order focal plane aberrations we need to compute the fourier transform explicitly
            if np.any([B20, B2m2, B2p2]!=0):
                sigma_fib = lam0/d1/np.pi/MFR/mas
                sigma_ref = 2.2e-6/d1/np.pi/MFR/mas
                zernike = 0
                zernike += B1m1*2*(aa/sigma_ref)*np.sin(t)
                zernike += B1p1*2*(aa/sigma_ref)*np.cos(t)
                zernike += B20 *np.sqrt(3.)*(2.*(aa/sigma_ref)**2 - 1)
                zernike += B2m2*np.sqrt(6.)*(aa/sigma_ref)**2*np.sin(2.*t)
                zernike += B2p2*np.sqrt(6.)*(aa/sigma_ref)**2*np.cos(2.*t)

                fiber = np.exp(-0.5*(aa/sigma_fib)**2) * np.exp(2.*np.pi/lam0*1j*zernike*1e-6)
                fiber = np.fft.fft2(fiber)

            
            #--- phase screens (pupil plane) ---#
            zernike  = A00
            zernike += A1m1*2*(2.*r/d1)*np.sin(t)
            zernike += A1p1*2*(2.*r/d1)*np.cos(t)
            zernike += A2m2*np.sqrt(6.)*(2.*r/d1)**2*np.sin(2.*t)
            zernike += A2p2*np.sqrt(6.)*(2.*r/d1)**2*np.cos(2.*t)
            zernike += A20 *np.sqrt(3.)*(2.*(2.*r/d1)**2 - 1)
            zernike += A3m1*np.sqrt(8.)*(3.*(2.*r/d1)**3 - 2.*(2.*r/d1))*np.sin(t)
            zernike += A3p1*np.sqrt(8.)*(3.*(2.*r/d1)**3 - 2.*(2.*r/d1))*np.cos(t)
            zernike += A3m3*np.sqrt(8.)*(2.*r/d1)**3*np.sin(3.*t)
            zernike += A3p3*np.sqrt(8.)*(2.*r/d1)**3*np.cos(3.*t)
            zernike += A4m2*np.sqrt(10.)*(2.*r/d1)**4*np.sin(4.*t)
            zernike += A4p2*np.sqrt(10.)*(2.*r/d1)**4*np.cos(4.*t)
            zernike += A4m4*np.sqrt(10.)*(4.*(2.*r/d1)**4 -3.*(2.*r/d1)**2)*np.sin(2.*t)
            zernike += A4p4*np.sqrt(10.)*(4.*(2.*r/d1)**4 -3.*(2.*r/d1)**2)*np.cos(2.*t)
            zernike += A40*np.sqrt(5.)*(6.*(2.*r/d1)**4 - 6.*(2.*r/d1)**2 + 1)
            zernike += A5m1*2.*np.sqrt(3.)*(10*(2.*r/d1)**5 - 12*(2.*r/d1)**3 + 3.*2.*r/d1)*np.sin(t)
            zernike += A5p1*2.*np.sqrt(3.)*(10*(2.*r/d1)**5 - 12*(2.*r/d1)**3 + 3.*2.*r/d1)*np.cos(t)
            zernike += A5m3*2.*np.sqrt(3.)*(5.*(2.*r/d1)**5 - 4.*(2.*r/d1)**3)*np.sin(3.*t)
            zernike += A5p3*2.*np.sqrt(3.)*(5.*(2.*r/d1)**5 - 4.*(2.*r/d1)**3)*np.cos(3.*t)
            zernike += A5m5*2.*np.sqrt(3.)*(2.*r/d1)**5*np.sin(5*t)
            zernike += A5p5*2.*np.sqrt(3.)*(2.*r/d1)**5*np.cos(5*t)
            zernike += A6m6*np.sqrt(14.)*(2.*r/d1)**6*np.sin(6.*t)
            zernike += A6p6*np.sqrt(14.)*(2.*r/d1)**6*np.cos(6.*t)
            zernike += A6m4*np.sqrt(14.)*(6.*(2.*r/d1)**6 - 5.*(2.*r/d1)**4)*np.sin(4.*t)
            zernike += A6p4*np.sqrt(14.)*(6.*(2.*r/d1)**6 - 5.*(2.*r/d1)**4)*np.cos(4.*t)
            zernike += A6m2*np.sqrt(14.)*(15.*(2.*r/d1)**6 - 20.*(2.*r/d1)**4 - 6.*(2.*r/d1)**2)*np.sin(2.*t)
            zernike += A6p2*np.sqrt(14.)*(15.*(2.*r/d1)**6 - 20.*(2.*r/d1)**4 - 6.*(2.*r/d1)**2)*np.cos(2.*t)
            zernike += A60*np.sqrt(7.)*(20.*(2.*r/d1)**6 - 30.*(2.*r/d1)**4 +12*(2.*r/d1)**2 - 1)
            
            phase = 2.*np.pi/lam0*zernike*1.e-6

            #--- transform to image plane ---#
            complexPsf = np.fft.fftshift(np.fft.fft2(pupil * fiber * np.exp(1j*phase) ))
            return complexPsf[cc,cc]/np.abs(complexPsf[cc,cc]).max()
            
        
        zernikefile = resource_filename('gravipy', 'Phasemaps/' + zerfile)
        zer = np.load(zernikefile, allow_pickle=True).item()

        wave = self.wlSC
        
        if self.tel == 'UT':
            stopB=8.0
            stopS=0.96
            dalpha=1
            totN=1024
            d = 8
            amax = 100
            set_smooth = smooth

        elif self.tel == 'AT':
            stopB = 8.0/4.4
            stopS = 8.0/4.4*0.076
            dalpha = 1.*4.4
            totN = 1024
            d = 1.8
            amax = 100*4.4
            set_smooth = smooth #/ 4.4

        kernel = Gaussian2DKernel(x_stddev=smooth)
         
        print('Creating phasemaps:')
        print('StopB : %.2f' % stopB)
        print('StopS : %.2f' % stopS)
        print('Smooth: %.2f' % set_smooth)
        print('amax: %i' % amax)

        if nthreads == 1:
            all_pm = np.zeros((len(wave), 4, 201, 201),
                            dtype=np.complex_)
            all_pm_denom = np.zeros((len(wave), 4, 201, 201),
                                    dtype=np.complex_)
            for wdx, wl in enumerate(wave):
                print_status(wdx, len(wave))
                for GV in range(4):
                    zer_GV = zer['GV%i' % (GV+1)]
                    pm = phase_screen(*zer_GV, lam0=wl, d1=d, stopB=stopB, stopS=stopS, 
                                      dalpha=dalpha, totN=totN, amax=amax)
                    if pm.shape != (201, 201):
                        print(pm.shape)
                        print('Need to convert to (201,201) shape')
                        pm = procrustes(pm, (201,201), padval=0)
                    pm_sm = signal.convolve2d(pm, kernel, mode='same')
                    pm_sm_denom = signal.convolve2d(np.abs(pm)**2, kernel, mode='same')
                    
                    all_pm[wdx, GV] = pm_sm
                    all_pm_denom[wdx, GV] = pm_sm_denom
                    
                    if plot and wdx == 0:
                        plt.imshow(np.abs(pm_sm))
                        plt.colorbar()
                        plt.show()
                        plt.imshow(np.angle(pm_sm))
                        plt.colorbar()
                        plt.show()
                        
        else:
            def multi_pm(lam):
                print(lam)
                m_all_pm = np.zeros((4, 201, 201), dtype=np.complex_)
                m_all_pm_denom = np.zeros((4, 201, 201), dtype=np.complex_)
                for GV in range(4):
                    zer_GV = zer['GV%i' % (GV+1)]
                    pm = phase_screen(*zer_GV, lam0=lam, d1=d, stopB=stopB, stopS=stopS, 
                                      dalpha=dalpha, totN=totN, amax=amax)

                    if pm.shape != (201, 201):
                        print('Need to convert to (201,201) shape')
                        print(pm.shape)
                        pm = procrustes(pm, (201,201), padval=0)

                    pm_sm = signal.convolve2d(pm, kernel, mode='same')
                    pm_sm_denom = signal.convolve2d(np.abs(pm)**2, kernel, mode='same')
                    m_all_pm[GV] = pm_sm
                    m_all_pm_denom[GV] = pm_sm_denom
                return np.array([m_all_pm, m_all_pm_denom])
            
            res = np.array(Parallel(n_jobs=nthreads)(delayed(multi_pm)(lam) for lam in wave))
            
            all_pm = res[:,0,:,:,:]
            all_pm_denom = res[:,1,:,:,:]
        if datayear == 2019:
            savename = 'Phasemaps/Phasemap_%s_%s_Smooth%i.npy' % (self.tel, self.resolution, smooth)
            savename2 = 'Phasemaps/Phasemap_%s_%s_Smooth%i_denom.npy' % (self.tel, self.resolution, smooth)
        else:
            savename = 'Phasemaps/Phasemap_%s_%s_Smooth%i_2020data.npy' % (self.tel, self.resolution, smooth)
            savename2 = 'Phasemaps/Phasemap_%s_%s_Smooth%i_2020data_denom.npy' % (self.tel, self.resolution, smooth)
        savefile = resource_filename('gravipy', savename)
        np.save(savefile, all_pm)
        savefile = resource_filename('gravipy', savename2)
        np.save(savefile, all_pm_denom)


    def rotation(self, ang):
        """
        Rotation matrix, needed for phasemaps
        """
        return np.array([[np.cos(ang), np.sin(ang)],
                         [-np.sin(ang), np.cos(ang)]])
    
    
    def loadPhasemaps(self, interp, tofits=False):
        smoothkernel = self.smoothkernel
        datayear = self.datayear
        if datayear == 2019:
            pm1_file = 'Phasemaps/Phasemap_%s_%s_Smooth%i.npy' % (self.tel, self.resolution, smoothkernel)
            pm2_file = 'Phasemaps/Phasemap_%s_%s_Smooth%i_denom.npy' % (self.tel, self.resolution, smoothkernel)
        elif datayear == 2020:
            pm1_file = 'Phasemaps/Phasemap_%s_%s_Smooth%i_2020data.npy' % (self.tel, self.resolution, smoothkernel)
            pm2_file = 'Phasemaps/Phasemap_%s_%s_Smooth%i_2020data_denom.npy' % (self.tel, self.resolution, smoothkernel)
            
        try:
            pm1 = np.load(resource_filename('gravipy', pm1_file))
            pm2 = np.real(np.load(resource_filename('gravipy', pm2_file)))
        except FileNotFoundError:
            raise ValueError('%s does not exist, you have to create the phasemap first!' % pm1_file)

        wave = self.wlSC
        if pm1.shape[0] != len(wave):
            raise ValueError('Phasemap and data have different numbers of channels')

        self.amp_map = np.abs(pm1)
        self.pha_map = np.angle(pm1, deg=True)
        self.amp_map_denom = pm2

        for wdx in range(len(wave)):
            for tel in range(4):
                self.amp_map[wdx,tel] /= np.max(self.amp_map[wdx,tel])
                self.amp_map_denom[wdx,tel] /= np.max(self.amp_map_denom[wdx,tel])
                
        if tofits:
            primary_hdu = fits.PrimaryHDU()
            hlist = [primary_hdu]
            for tel in range(4):
                hlist.append(fits.ImageHDU(self.amp_map[:,tel], 
                                           name='SC_AMP UT%i' % (4-tel)))
                hlist.append(fits.ImageHDU(self.pha_map[:,tel], 
                                           name='SC_PHA UT%i' % (4-tel)))
            hdul = fits.HDUList(hlist)
            hdul.writeto(resource_filename('gravipy', 'testfits.fits'),
                         overwrite=True)
            print('Saving phasemaps as fits file to: %s' 
                  % resource_filename('gravipy', 'testfits.fits'))
                
        if interp:
            x = np.arange(201)
            y = np.arange(201)
            itel = np.arange(4)
            iwave = np.arange(len(wave))
            points = (iwave, itel, x, y)
            
            self.amp_map_int = interpolate.RegularGridInterpolator(points, self.amp_map) 
            self.pha_map_int = interpolate.RegularGridInterpolator(points, self.pha_map) 
            self.amp_map_denom_int = interpolate.RegularGridInterpolator(points, self.amp_map_denom) 
            
            #self.amp_map_int = np.zeros((len(wave),4), dtype=object)
            #self.pha_map_int = np.zeros((len(wave),4), dtype=object)
            #self.amp_map_denom_int = np.zeros((len(wave),4), dtype=object)
            #for tel in range(4):
                #for wdx in range(len(wave)):
                    #self.amp_map_int[wdx, tel] = interpolate.interp2d(x, y, self.amp_map[wdx, tel])
                    #self.pha_map_int[wdx, tel] = interpolate.interp2d(x, y, self.pha_map[wdx, tel])
                    #self.amp_map_denom_int[wdx, tel] = interpolate.interp2d(x, y, self.amp_map_denom[wdx, tel])
                

    def readPhasemaps(self, ra, dec, fromFits=True, 
                      northangle=None, dra=None, ddec=None,
                      interp=True, givepos=False):
        """
        Calculates coupling amplitude / phase for given coordinates
        ra,dec: RA, DEC position on sky relative to nominal field center = SOBJ [mas]
        dra,ddec: ESO QC MET SOBJ DRA / DDEC: 
            location of science object (= desired science fiber position, = field center) 
            given by INS.SOBJ relative to *actual* fiber position measured by the laser metrology [mas]
            mis-pointing = actual - desired fiber position = -(DRA,DDEC)
        north_angle: north direction on acqcam in degree
        if fromFits is true, northangle & dra,ddec are taken from fits file
        """
        if fromFits:
            # should not do that in here for mcmc
            header = fits.open(self.name)[0].header
            northangle1 = header['ESO QC ACQ FIELD1 NORTH_ANGLE']/180*math.pi
            northangle2 = header['ESO QC ACQ FIELD2 NORTH_ANGLE']/180*math.pi
            northangle3 = header['ESO QC ACQ FIELD3 NORTH_ANGLE']/180*math.pi
            northangle4 = header['ESO QC ACQ FIELD4 NORTH_ANGLE']/180*math.pi
            northangle = [northangle1, northangle2, northangle3, northangle4]

            ddec1 = header['ESO QC MET SOBJ DDEC1']
            ddec2 = header['ESO QC MET SOBJ DDEC2']
            ddec3 = header['ESO QC MET SOBJ DDEC3']
            ddec4 = header['ESO QC MET SOBJ DDEC4']
            ddec = [ddec1, ddec2, ddec3, ddec4]

            dra1 = header['ESO QC MET SOBJ DRA1']
            dra2 = header['ESO QC MET SOBJ DRA2']
            dra3 = header['ESO QC MET SOBJ DRA3']
            dra4 = header['ESO QC MET SOBJ DRA4']
            dra = [dra1, dra2, dra3, dra4]
            
        wave = self.wlSC
        
        pm_pos = np.zeros((4, 2))
        readout_pos = np.zeros((4*len(wave),4))
        readout_pos[:,0] = np.tile(np.arange(len(wave)),4)                                                  
        readout_pos[:,1] = np.repeat(np.arange(4),len(wave))

        for tel in range(4):
            pos = np.array([ra + dra[tel], dec + ddec[tel]])
            if self.tel == 'AT':
                pos /= 4.4
            try:
                pos[0] += self.pm_pos_off[0]
                pos[1] += self.pm_pos_off[1]
            except (NameError, AttributeError):
                pass
            pos_rot = np.dot(self.rotation(northangle[tel]), pos) + 100
            readout_pos[readout_pos[:,1]==tel,2] = pos_rot[1]
            readout_pos[readout_pos[:,1]==tel,3] = pos_rot[0]
            pm_pos[tel] = pos_rot
            
        cor_amp = self.amp_map_int(readout_pos).reshape(4,len(wave))
        cor_pha = self.pha_map_int(readout_pos).reshape(4,len(wave))
        cor_int_denom = self.amp_map_denom_int(readout_pos).reshape(4,len(wave))
        
        if givepos:
            return readout_pos
        else:
            return cor_amp, cor_pha, cor_int_denom 














class GravMFit(GravData, GravPhaseMaps):

    def vis_intensity_approx(self, s, alpha, lambda0, dlambda):
        """
        Approximation for Modulated interferometric intensity
        s:      B*skypos-opd1-opd2
        alpha:  power law index
        lambda0:zentral wavelength
        dlambda:size of channels 
        """
        x = 2*s*dlambda/lambda0**2.
        sinc = np.sinc(x)  # be aware that np.sinc = np.sin(pi*x)/(pi*x)
        return (lambda0/2.2)**(-1-alpha)*2*dlambda*sinc*np.exp(-2.j*np.pi*s/lambda0)
    
    def vis_intensity(self, s, alpha, lambda0, dlambda):
        """
        Analytic solution for Modulated interferometric intensity
        s:      B*skypos-opd1-opd2
        alpha:  power law index
        lambda0:zentral wavelength
        dlambda:size of channels 
        """
        x1 = lambda0+dlambda
        x2 = lambda0-dlambda
        if not np.isscalar(lambda0):
            if not np.isscalar(s):
                res = np.zeros(len(lambda0), dtype=np.complex_)
                for idx in range(len(lambda0)):
                    if s[idx] == 0 and alpha == 0:
                        res[idx] = self.vis_intensity_num(s[idx], alpha, 
                                                          lambda0[idx], dlambda[idx])
                    else:
                        up = self.vis_int_full(s[idx], alpha, x1[idx])
                        low = self.vis_int_full(s[idx], alpha, x2[idx])
                        res[idx] = up - low
            else:
                res = np.zeros(len(lambda0), dtype=np.complex_)
                for idx in range(len(lambda0)):
                    if s == 0 and alpha == 0:
                        res[idx] = self.vis_intensity_num(s, alpha, lambda0[idx], 
                                                          dlambda[idx])
                    else:
                        up = self.vis_int_full(s, alpha, x1[idx])
                        low = self.vis_int_full(s, alpha, x2[idx])
                        res[idx] = up - low
        else:
            if s == 0 and alpha == 0:
                res = self.vis_intensity_num(s, alpha, lambda0, dlambda)
            else:
                up = self.vis_int_full(s, alpha, x1)
                low = self.vis_int_full(s, alpha, x2)
                res = up - low
        return res
        
    def vis_int_full(self, s, alpha, difflam):
        if s == 0:
            return -2.2**(1 + alpha)/alpha*difflam**(-alpha)
        a = difflam*(difflam/2.2)**(-1-alpha)
        bval = mpmath.gammainc(alpha, (2*1j*np.pi*s/difflam))
        b = float(bval.real)+float(bval.imag)*1j
        c = (2*np.pi*1j*s/difflam)**alpha
        return (a*b/c)
    
    
    def visibility_integrator(self, wave, s, alpha):
        """
        complex integral to be integrated over wavelength
        wave in [micron]
        theta holds the exponent alpha, and the seperation s
        """
        return (wave/2.2)**(-1-alpha)*np.exp(-2*np.pi*1j*s/wave)
    
    
    def vis_intensity_num(self, s, alpha, lambda0, dlambda):
        """
        Dull numeric solution for Modulated interferometric intensity
        s:      B*skypos-opd1-opd2
        alpha:  power law index
        lambda0:zentral wavelength
        dlambda:size of channels 
        """
        if np.all(s == 0.) and alpha != 0:
            return -2.2**(1 + alpha)/alpha*(lambda0+dlambda)**(-alpha) - (-2.2**(1 + alpha)/alpha*(lambda0-dlambda)**(-alpha))
        else:
            return complex_quadrature_num(self.visibility_integrator, lambda0-dlambda, lambda0+dlambda, (s, alpha))
        
        
        
    
    def ind_visibility(self, s, alpha, wave, dlambda):
        mode = self.fit_mode
        if mode == "approx":
            ind_vis = self.vis_intensity_approx(s, alpha, wave, dlambda)
        elif mode == "analytic":
            ind_vis = self.vis_intensity(s, alpha, wave, dlambda)
        elif mode == "numeric":
            ind_vis = self.vis_intensity_num(s, alpha, wave, dlambda)
        else:
            raise ValueError('approx has to be approx, analytic or numeric')
        return ind_vis
    
    

    def phasemap_source(self, x, y, northA, dra, ddec):
        amp, pha, inten = self.readPhasemaps(x, y, fromFits=False, 
                                           northangle=northA, dra=dra, ddec=ddec,
                                           interp=self.interppm)
        pm_amp = np.array([[amp[0], amp[1]],
                            [amp[0], amp[2]],
                            [amp[0], amp[3]],
                            [amp[1], amp[2]],
                            [amp[1], amp[3]],
                            [amp[2], amp[3]]])
        pm_pha = np.array([[pha[0], pha[1]],
                            [pha[0], pha[2]],
                            [pha[0], pha[3]],
                            [pha[1], pha[2]],
                            [pha[1], pha[3]],
                            [pha[2], pha[3]]])
        pm_int = np.array([[inten[0], inten[1]],
                            [inten[0], inten[2]],
                            [inten[0], inten[3]],
                            [inten[1], inten[2]],
                            [inten[1], inten[3]],
                            [inten[2], inten[3]]])
        return pm_amp, pm_pha, pm_int
    
        


    def calc_vis(self, theta):
        mas2rad = 1e-3 / 3600 / 180 * np.pi
        rad2mas = 180 / np.pi * 3600 * 1e3

        u = self.u
        v = self.v
        wave = self.wave
        dlambda = self.dlambda
        nsource = self.nsource
        
        fixedBHalpha = self.fixedBHalpha

        phasemaps = self.phasemaps
        if phasemaps:
            northA = self.northangle
            ddec = self.ddec
            dra = self.dra
        
        pos_RA = []
        pos_DEC = []
        fr = []
        for ndx in range(nsource):
            pos_RA.append(theta[ndx*3])
            pos_DEC.append(theta[ndx*3+1])
            fr.append(10.**(theta[ndx*3+2]))
        th_rest = nsource*3
        
        if fixedBHalpha:
            alpha_SgrA = -0.5
        else:
            alpha_SgrA = theta[th_rest]
            
        fluxRatioBG = theta[th_rest+1]
        alpha_bg = 3.
        
        pc_RA = theta[th_rest+2]
        pc_DEC = theta[th_rest+3]

        try:
            alpha_stars = self.alpha_stars
            if self.verbose:
                print('Alpha of second star is %.2f' % alpha_S2)
        except:
            alpha_stars = 3
        
        if phasemaps:
            pm_amp_c, pm_pha_c, pm_int_c = self.phasemap_source(pc_RA, pc_DEC, 
                                                        northA, dra, ddec)
        else:
            pm_amp_c = np.ones((6, 2))
            pm_pha_c = np.zeros((6, 2))
            pm_int_c = np.ones((6, 2))
            
        pm_sources = []
        for ndx in range(nsource):
            if phasemaps:
                pm_amp, pm_pha, pm_int = self.phasemap_source(pc_RA + pos_RA[ndx], 
                                                         pc_DEC + pos_DEC[ndx], 
                                                         northA, dra, ddec)
            else:
                pm_amp = np.ones((6, 2))
                pm_pha = np.zeros((6, 2))
                pm_int = np.ones((6, 2))
            pm_sources.append([pm_amp, pm_pha, pm_int])
            

        vis = np.zeros((6,len(wave))) + 0j
        for i in range(0,6):
            try:
                if self.fit_for[3] == 0:
                    pc_RA = 0
                    pc_DEC = 0
            except AttributeError:
                pass
            
            s_SgrA = ((pc_RA)*u[i] + (pc_DEC)*v[i]) * mas2rad * 1e6
            if phasemaps:
                s_SgrA -= ((pm_pha_c[i,0] - pm_pha_c[i,1])/360*wave)
            
            s_stars = []
            for ndx in range(nsource):
                s_s = ((pos_RA[ndx] + pc_RA)*u[i] + 
                       (pos_DEC[ndx] + pc_DEC)*v[i]) * mas2rad * 1e6
                
                if phasemaps:
                    _, pm_pha, _ = pm_sources[ndx]
                    s_s -= ((pm_pha[i,0] - pm_pha[i,1])/360*wave)
                s_stars.append(s_s)

            intSgrA = self.ind_visibility(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
            intSgrA_center = self.ind_visibility(0, alpha_SgrA, wave, dlambda[i,:])

            nom = intSgrA
            denom1 = np.copy(intSgrA_center)
            denom2 = np.copy(intSgrA_center)
            
            int_star_center = self.ind_visibility(0, alpha_stars, wave, dlambda[i,:])
            for ndx in range(nsource):
                int_star = self.ind_visibility(s_stars[ndx], alpha_stars, wave, dlambda[i,:])
                
                pm_amp, _, pm_int = pm_sources[ndx]
                cr1 = (pm_amp[i,0] / pm_amp_c[i,0])**2
                cr2 = (pm_amp[i,1] / pm_amp_c[i,1])**2
                cr_denom1 = (pm_int[i,0] / pm_int_c[i,0])
                cr_denom2 = (pm_int[i,1] / pm_int_c[i,1])
                
                nom += (fr[ndx] * np.sqrt(cr1*cr2) * int_star)
                denom1 += (fr[ndx] * cr_denom1 * int_star_center)
                denom2 += (fr[ndx] * cr_denom2 * int_star_center)
                
            intBG = self.ind_visibility(0, alpha_bg, wave, dlambda[i,:])
            denom1 += (fluxRatioBG * intBG)
            denom2 += (fluxRatioBG * intBG)
            
            vis[i,:] = nom / (np.sqrt(denom1)*np.sqrt(denom2))
            
        visamp = np.abs(vis)
        visphi = np.angle(vis, deg=True)
        closure = np.zeros((4, len(wave)))
        for idx in range(4):
            closure[idx] = visphi[self.bispec_ind[idx,0]] + visphi[self.bispec_ind[idx,1]] - visphi[self.bispec_ind[idx,2]]

        visphi = visphi + 360.*(visphi<-180.) - 360.*(visphi>180.)
        closure = closure + 360.*(closure<-180.) - 360.*(closure>180.)
        return visamp, visphi, closure
                


    def lnprob(self, theta, fitdata, lower, upper):
        if np.any(theta < lower) or np.any(theta > upper):
            return -np.inf
        return self.lnlike(theta, fitdata)
    
        
    def lnlike(self, theta, fitdata):
        # Model
        model_visamp, model_visphi, model_closure = self.calc_vis(theta)
        model_vis2 = model_visamp**2.
        
        #Data
        (visamp, visamp_error, visamp_flag,
         vis2, vis2_error, vis2_flag,
         closure, closure_error, closure_flag,
         visphi, visphi_error, visphi_flag) = fitdata
        
        res_visamp = np.sum(-(model_visamp-visamp)**2/visamp_error**2*(1-visamp_flag))
        res_vis2 = np.sum(-(model_vis2-vis2)**2./vis2_error**2.*(1-vis2_flag))
        
        res_closure_1 = np.abs(model_closure-closure)
        res_closure_2 = 360-np.abs(model_closure-closure)
        check = np.abs(res_closure_1) < np.abs(res_closure_2)
        res_closure = res_closure_1*check + res_closure_2*(1-check)
        res_clos = np.sum(-res_closure**2./closure_error**2.*(1-closure_flag))
 
        res_visphi_1 = np.abs(model_visphi-visphi)
        res_visphi_2 = 360-np.abs(model_visphi-visphi)
        check = np.abs(res_visphi_1) < np.abs(res_visphi_2) 
        res_visphi = res_visphi_1*check + res_visphi_2*(1-check)
        res_phi = np.sum(-res_visphi**2./visphi_error**2.*(1-visphi_flag))
        
        ln_prob_res = 0.5 * (res_visamp * self.fit_for[0] + 
                             res_vis2 * self.fit_for[1] + 
                             res_clos * self.fit_for[2] + 
                             res_phi * self.fit_for[3])
        return ln_prob_res 



    def fitStars(self, 
                 ra_list, 
                 de_list,
                 fr_list,
                 fit_size=None,
                 fit_pos=None,
                 fit_fr=None,
                 nthreads=4, 
                 nwalkers=500, 
                 nruns=500, 
                 bestchi=True,
                 bequiet=False,
                 fit_for=np.array([0.5,0.5,1.0,0.0]), 
                 fit_mode='analytic', 
                 no_fit=False, 
                 no_fit_values=None, 
                 onlypol=None, 
                 initial=None, 
                 flagtill=3, 
                 flagfrom=13,
                 fixedBHalpha=False, 
                 plotCorner=True, 
                 plotScience=True, 
                 createpdf=False, 
                 writeresults=False, 
                 outputdir='./',
                 redchi2=False,
                 phasemaps=False,
                 interppm=True,
                 smoothkernel=15,
                 pmdatayear=2019):
        '''
        Multi source fit to GRAVITY data
        Function fits a central source and a number of companion sources.
        All flux ratios are with respect to centra source
        
        The length of the input lists defines number of companions!
        
        Mandatory argumens:
        ra_list:        Initial guess for ra separation of companions
        de_list:        Initial guess for dec separation of companions
        fr_list:        Initial guess for flux ratio of companions
        
        Optional arguments for companions:
        If those vaues are given they need to be a list with one entry per companion
        fit_size:       Size of fitting area [5]
        fit_pos:        Fit position of each companion [True]
        fit_fr:         Fit flux ratio of each companion [True]
        
        Other optional arguments:
        nthreads:       number of cores [4] 
        nwalkers:       number of walkers [500] 
        nruns:          number of MCMC runs [500] 
        bestchi:        Gives best chi2 (for True) or mcmc res as output [True]
        bequiet:        Suppresses ALL outputs
        fit_for:        weight of VA, V2, T3, VP [[0.5,0.5,1.0,0.0]] 
        fit_mode:       Kind of integration for visibilities (approx, numeric, analytic) [analytic]
        no_fit  :       Only gives fitting results for parameters from no_fit_values [False]
        no_fit_values:  has to be given for donotfit [None]
        onlypol:        Only fits one polarization for split mode, either 0 or 1 [None]
        initial:        Initial guess for fit [None]
        flagtill:       Flag blue channels, has to be changed for not LOW [3] 
        flagfrom:       Flag red channels, has to be changed for not LOW [13]
        fixedBHalpha:   Fit for black hole power law [False]
        plotCorner:     plot MCMC results [True]
        plotScience:    plot fit result [True]
        createpdf:      Creates a pdf with fit results and all plots [False] 
        writeresults:   Write fit results in file [True]
        outputdir:      Directory where pdf & txt files are saved [./]
        redchi2:        Gives redchi2 instead of chi2 [False]
        phasemaps:      Use Phasemaps for fit [False]
        interppm:       Interpolate Phasemaps [True]
        smoothkernel:   Size of smoothing kernel in mas [15]
        simulate_pm:    Phasemaps for simulated data, sets ACQ parameter to 0 [False]
        '''
        
        if self.resolution != 'LOW' and flagtill == 3 and flagfrom == 13:
            raise ValueError('Initial values for flagtill and flagfrom have to be changed if not low resolution')
        
        self.fit_for = fit_for
        self.fixedBHalpha = fixedBHalpha
        self.interppm = interppm
        self.fit_mode = fit_mode
        self.bequiet = bequiet
        rad2as = 180 / np.pi * 3600
        
        
        self.phasemaps = phasemaps
        self.datayear = pmdatayear
        self.smoothkernel = smoothkernel
        if phasemaps:
            self.loadPhasemaps(interp=interppm)
            
            header = fits.open(self.name)[0].header
            northangle1 = header['ESO QC ACQ FIELD1 NORTH_ANGLE']/180*math.pi
            northangle2 = header['ESO QC ACQ FIELD2 NORTH_ANGLE']/180*math.pi
            northangle3 = header['ESO QC ACQ FIELD3 NORTH_ANGLE']/180*math.pi
            northangle4 = header['ESO QC ACQ FIELD4 NORTH_ANGLE']/180*math.pi
            self.northangle = [northangle1, northangle2, northangle3, northangle4]

            ddec1 = header['ESO QC MET SOBJ DDEC1']
            ddec2 = header['ESO QC MET SOBJ DDEC2']
            ddec3 = header['ESO QC MET SOBJ DDEC3']
            ddec4 = header['ESO QC MET SOBJ DDEC4']
            self.ddec = [ddec1, ddec2, ddec3, ddec4]

            dra1 = header['ESO QC MET SOBJ DRA1']
            dra2 = header['ESO QC MET SOBJ DRA2']
            dra3 = header['ESO QC MET SOBJ DRA3']
            dra4 = header['ESO QC MET SOBJ DRA4']
            self.dra = [dra1, dra2, dra3, dra4]

        nsource = len(ra_list)
        if fit_size is None:
            fit_size = np.ones(nsource)*5
        if fit_pos is None:
            fit_pos = np.ones(nsource)
        if fit_fr is None:
            fit_fr = np.ones(nsource)
        
        if len(de_list) != nsource or len(fr_list) != nsource or len(fit_size) != nsource:
            raise ValueError('list of input parameters have different lengths')
        if len(fit_pos) != nsource or len(fit_fr) != nsource:
            raise ValueError('list of input parameters have different lengths')
        self.nsource = nsource
        
        # Get data from file
        tel = fits.open(self.name)[0].header["TELESCOP"]
        if tel == 'ESO-VLTI-U1234':
            self.tel = 'UT'
        elif tel == 'ESO-VLTI-A1234':
            self.tel = 'AT'
        else:
            raise ValueError('Telescope not AT or UT, something wrong with input data')

        try:
            self.fiberOffX = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFX"] 
            self.fiberOffY = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFY"]
        except KeyError:
            self.fiberOffX = 0
            self.fiberOffY = 0

        nwave = self.channel

        self.getIntdata(plot=False, flag=False)

        MJD = fits.open(self.name)[0].header["MJD-OBS"]
        u = self.u
        v = self.v
        wave = self.wlSC
        self.wave = wave
        self.getDlambda()
        dlambda = self.dlambda
        
        stname = self.name.find('GRAVI')
        if phasemaps:
            txtfilename = outputdir + 'pm_sourcefit_' + self.name[stname:-5] + '.txt'
        else:
            txtfilename = outputdir + 'sourcefit_' + self.name[stname:-5] + '.txt'
        if writeresults and not no_fit:
            txtfile = open(txtfilename, 'w')
            txtfile.write('# Results of source fit for %s \n' % self.name[stname:])
            txtfile.write('# Lines are: Best chi2, MCMC result, MCMC error -, MCMC error + \n')
            txtfile.write('# Rows are: dRA, dDEC, f1, f2, f3, f4, alpha flare, f BG, alpha BG, PC RA, PC DEC, OPD1, OPD2, OPD3, OPD4 \n')
            txtfile.write('# Parameter which are not fitted have 0.0 as error \n')
            txtfile.write('# MJD: %f \n' % MJD)
            txtfile.write('# OFFX: %f \n' % self.fiberOffX)
            txtfile.write('# OFFY: %f \n\n' % self.fiberOffY)

        results = []

        # Initial guesses
        if initial is not None:
            if len(initial) != 4:
                raise ValueError('Length of initial parameter list is not correct')
            alpha_SgrA_in, flux_ratio_bg_in, pc_RA_in, pc_DEC_in = initial
        else:
            alpha_SgrA_in = -0.5
            flux_ratio_bg_in = 0.1
            pc_RA_in = 0
            pc_DEC_in = 0
            
            
        theta = np.zeros(nsource*3+4)
        lower = np.zeros(nsource*3+4)
        upper = np.zeros(nsource*3+4)
        todel = []
        
        for ndx in range(nsource):
            theta[ndx*3] = ra_list[ndx]
            theta[ndx*3+1] = de_list[ndx]
            theta[ndx*3+2] = fr_list[ndx]

            lower[ndx*3] = ra_list[ndx] - fit_size[ndx]
            lower[ndx*3+1] = de_list[ndx] - fit_size[ndx]
            lower[ndx*3+2] = np.log10(0.001)

            upper[ndx*3] = ra_list[ndx] + fit_size[ndx]
            upper[ndx*3+1] = de_list[ndx] + fit_size[ndx]
            upper[ndx*3+2] = np.log10(10.)
            
            if not fit_pos[ndx]:
                todel.append(ndx*3)
                todel.append(ndx*3+1)
            if not fit_fr[ndx]:
                todel.append(ndx*3+2)

        th_rest = nsource*3
        
        theta[th_rest] = alpha_SgrA_in
        theta[th_rest+1] = flux_ratio_bg_in
        theta[th_rest+2] = pc_RA_in
        theta[th_rest+3] = pc_DEC_in

        pc_size = 5
        lower[th_rest] = -10
        lower[th_rest+1] = 0.1
        lower[th_rest+2] = pc_RA_in - pc_size
        lower[th_rest+3] = pc_DEC_in - pc_size

        upper[th_rest] = 10
        upper[th_rest+1] = 20
        upper[th_rest+2] = pc_RA_in + pc_size
        upper[th_rest+3] = pc_DEC_in + pc_size
        
        theta_names = []
        for ndx in range(nsource):
            theta_names.append('dRA%i' % (ndx + 1))
            theta_names.append('dDEC%i' % (ndx + 1))
            theta_names.append('fr%i' % (ndx + 1))
        theta_names.append('alpha BH')
        theta_names.append('f BG')
        theta_names.append('pc RA')
        theta_names.append('pc Dec')
        

        ndim = len(theta)
        if fixedBHalpha:
            todel.append(th_rest)
        if fit_for[3] == 0:
            todel.append(th_rest+2)
            todel.append(th_rest+3)
        ndof = ndim - len(todel)
        
        if no_fit:
            if no_fit_values is None:
                raise ValueError('If no_fit is True, fit values have to be given by no_fit_values')
            if len(no_fit_values) != 4:
                print("alpha BH,  f BG, PC RA, PC DEC")
                raise ValueError('no_fit_values has to have 4 parameters, see above')
            plotCorner = False
            createpdf = False
            writeresults = False
            theta[-4:] = no_fit_values
            print('Will not fit the data, just print out the results for the given theta')

        # Get data
        if self.polmode == 'SPLIT':
            visamp_P = [self.visampSC_P1, self.visampSC_P2]
            visamp_error_P = [self.visamperrSC_P1, self.visamperrSC_P2]
            visamp_flag_P = [self.visampflagSC_P1, self.visampflagSC_P2]
            
            vis2_P = [self.vis2SC_P1, self.vis2SC_P2]
            vis2_error_P = [self.vis2errSC_P1, self.vis2errSC_P2]
            vis2_flag_P = [self.vis2flagSC_P1, self.vis2flagSC_P2]

            closure_P = [self.t3SC_P1, self.t3SC_P2]
            closure_error_P = [self.t3errSC_P1, self.t3errSC_P2]
            closure_flag_P = [self.t3flagSC_P1, self.t3flagSC_P2]
            
            visphi_P = [self.visphiSC_P1, self.visphiSC_P2]
            visphi_error_P = [self.visphierrSC_P1, self.visphierrSC_P2]
            visphi_flag_P = [self.visampflagSC_P1, self.visampflagSC_P2]

            closamp_P = [self.t3ampSC_P1, self.t3ampSC_P2]
            closamp_error_P = [self.t3amperrSC_P1, self.t3amperrSC_P2]
            closamp_flag_P = [self.t3ampflagSC_P1, self.t3ampflagSC_P2]

            ndit = np.shape(self.visampSC_P1)[0]//6
            if not bequiet:
                print('NDIT = %i' % ndit)
            if onlypol is not None:
                polnom = [onlypol]
            else:
                polnom = [0,1]
                
        elif self.polmode == 'COMBINED':
            visamp_P = [self.visampSC]
            visamp_error_P = [self.visamperrSC]
            visamp_flag_P = [self.visampflagSC]
            
            vis2_P = [self.vis2SC]
            vis2_error_P = [self.vis2errSC]
            vis2_flag_P = [self.vis2flagSC]

            closure_P = [self.t3SC]
            closure_error_P = [self.t3errSC]
            closure_flag_P = [self.t3flagSC]
            
            visphi_P = [self.visphiSC]
            visphi_error_P = [self.visphierrSC]
            visphi_flag_P = [self.visampflagSC]

            closamp_P = [self.t3ampSC]
            closamp_error_P = [self.t3amperrSC]
            closamp_flag_P = [self.t3ampflagSC]

            ndit = np.shape(self.visampSC)[0]//6
            if not bequiet:
                print('NDIT = %i' % ndit)
            polnom = [0]

        for dit in range(ndit):
            if writeresults and ndit > 1:
                txtfile.write('# DIT %i \n' % dit)
            if createpdf:
                savetime = str(datetime.now()).replace('-', '')
                savetime = savetime.replace(' ', '-')
                savetime = savetime.replace(':', '')
                self.savetime = savetime
                if phasemaps:
                    if ndit == 1:
                        pdffilename = outputdir + 'pm_binaryfit_' + self.name[stname:-5] + '.pdf'
                    else:
                        pdffilename = outputdir + 'pm_binaryfit_' + self.name[stname:-5] + '_DIT' + str(dit) + '.pdf'
                else:
                    if ndit == 1:
                        pdffilename = outputdir + 'binaryfit_' + self.name[stname:-5] + '.pdf'
                    else:
                        pdffilename = outputdir + 'binaryfit_' + self.name[stname:-5] + '_DIT' + str(dit) + '.pdf'
                pdf = FPDF(orientation='P', unit='mm', format='A4')
                pdf.add_page()
                pdf.set_font("Helvetica", size=12)
                pdf.set_margins(20,20)
                if ndit == 1:
                    pdf.cell(0, 10, txt="Fit report for %s" % self.name[stname:], ln=2, align="C", border='B')
                else:
                    pdf.cell(0, 10, txt="Fit report for %s, dit %i" % (self.name[stname:], dit), ln=2, align="C", border='B')
                pdf.ln()
                pdf.cell(40, 6, txt="Fringe Tracker", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=self.header["ESO FT ROBJ NAME"], ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Science Object", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=self.header["ESO INS SOBJ NAME"], ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Science Offset X", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(self.header["ESO INS SOBJ OFFX"]), 
                        ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Science Offset Y", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(self.header["ESO INS SOBJ OFFY"]), 
                        ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Fit for Visamp", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fit_for[0]), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Fit for Vis2", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fit_for[1]), ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Fit for cl. Phase", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fit_for[2]), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Fit for Visphi", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fit_for[3]), ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Flag before/after", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(flagtill) + '/' + str(flagfrom), 
                        ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Result: Best Chi2", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(bestchi), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Phasemaps", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(phasemaps), ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Integral solved by", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=fit_mode, ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Smoothing FWHM", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(smoothkernel), ln=1, align="L", border=0)
                pdf.ln()
            
            if not bequiet and not no_fit:
                print('Run MCMC for DIT %i' % (dit+1))
            ditstart = dit*6
            ditstop = ditstart + 6
            t3ditstart = dit*4
            t3ditstop = t3ditstart + 4
            
            for idx in polnom:
                visamp = visamp_P[idx][ditstart:ditstop]
                visamp_error = visamp_error_P[idx][ditstart:ditstop]
                visamp_flag = visamp_flag_P[idx][ditstart:ditstop]
                vis2 = vis2_P[idx][ditstart:ditstop]
                vis2_error = vis2_error_P[idx][ditstart:ditstop]
                vis2_flag = vis2_flag_P[idx][ditstart:ditstop]
                closure = closure_P[idx][t3ditstart:t3ditstop]
                closure_error = closure_error_P[idx][t3ditstart:t3ditstop]
                closure_flag = closure_flag_P[idx][t3ditstart:t3ditstop]
                visphi = visphi_P[idx][ditstart:ditstop]
                visphi_error = visphi_error_P[idx][ditstart:ditstop]
                visphi_flag = visphi_flag_P[idx][ditstart:ditstop]
                closamp = closamp_P[idx][t3ditstart:t3ditstop]
                closamp_error = closamp_error_P[idx][t3ditstart:t3ditstop]
                closamp_flag = closamp_flag_P[idx][t3ditstart:t3ditstop]
                
                # further flag if visamp/vis2 if >1 or NaN, and replace NaN with 0 
                with np.errstate(invalid='ignore'):
                    visamp_flag1 = (visamp > 1) | (visamp < 1.e-5)
                visamp_flag2 = np.isnan(visamp)
                visamp_flag_final = ((visamp_flag) | (visamp_flag1) | (visamp_flag2))
                visamp_flag = visamp_flag_final
                visamp = np.nan_to_num(visamp)
                visamp_error[visamp_flag] = 1.
                closamp = np.nan_to_num(closamp)
                closamp_error[closamp_flag] = 1.
                
                with np.errstate(invalid='ignore'):
                    vis2_flag1 = (vis2 > 1) | (vis2 < 1.e-5) 
                vis2_flag2 = np.isnan(vis2)
                vis2_flag_final = ((vis2_flag) | (vis2_flag1) | (vis2_flag2))
                vis2_flag = vis2_flag_final
                vis2 = np.nan_to_num(vis2)
                vis2_error[vis2_flag] = 1.

                closure = np.nan_to_num(closure)
                visphi = np.nan_to_num(visphi)
                visphi_flag[np.where(visphi_error == 0)] = True
                visphi_error[np.where(visphi_error == 0)] = 100
                closure_flag[np.where(closure_error == 0)] = True
                closure_error[np.where(closure_error == 0)] = 100

                if ((flagtill > 0) and (flagfrom > 0)):
                    p = flagtill
                    t = flagfrom
                    if idx == 0 and dit == 0:
                        if not bequiet:
                            print('using channels from #%i to #%i' % (p, t))
                    visamp_flag[:,0:p] = True
                    vis2_flag[:,0:p] = True
                    visphi_flag[:,0:p] = True
                    closure_flag[:,0:p] = True
                    closamp_flag[:,0:p] = True

                    visamp_flag[:,t:] = True
                    vis2_flag[:,t:] = True
                    visphi_flag[:,t:] = True
                    closure_flag[:,t:] = True
                    closamp_flag[:,t:] = True
                                        
                width = 1e-1
                pos = np.ones((nwalkers,ndim))
                for par in range(ndim):
                    if par in todel:
                        pos[:,par] = theta[par]
                    else:
                        pos[:,par] = theta[par] + width*np.random.randn(nwalkers)
                        
                if not bequiet:
                    if not no_fit:
                        print('Run MCMC for Pol %i' % (idx+1))
                    else:
                        print('Pol %i' % (idx+1))
                fitdata = [visamp, visamp_error, visamp_flag,
                            vis2, vis2_error, vis2_flag,
                            closure, closure_error, closure_flag,
                            visphi, visphi_error, visphi_flag]
                
                if not no_fit:
                    if nthreads == 1:
                        sampler = emcee.EnsembleSampler(nwalkers, ndim, self.lnprob, 
                                                            args=(fitdata, lower,
                                                                  upper))
                        if bequiet:
                            sampler.run_mcmc(pos, nruns, progress=False)
                        else:
                            sampler.run_mcmc(pos, nruns, progress=True)
                    else:
                        with Pool(processes=nthreads) as pool:
                            sampler = emcee.EnsembleSampler(nwalkers, ndim, self.lnprob, 
                                                            args=(fitdata, lower,
                                                                  upper),
                                                            pool=pool)
                            if bequiet:
                                sampler.run_mcmc(pos, nruns, progress=False) 
                            else:
                                sampler.run_mcmc(pos, nruns, progress=True)     
                            
                    if not bequiet:
                        print("---------------------------------------")
                        print("Mean acceptance fraction: %.2f"  
                              % np.mean(sampler.acceptance_fraction))
                        print("---------------------------------------")
                    if createpdf:
                        pdf.cell(0, 10, txt="Polarization  %i" % (idx+1), ln=2, align="C", border='B')
                        pdf.cell(0, 10, txt="Mean acceptance fraction: %.2f"  %
                                np.mean(sampler.acceptance_fraction), 
                                ln=2, align="L", border=0)
                    samples = sampler.chain
                    mostprop = sampler.flatchain[np.argmax(sampler.flatlnprobability)]

                    clsamples = np.delete(samples, todel, 2)
                    cllabels = np.delete(theta_names, todel)
                    clmostprop = np.delete(mostprop, todel)
                    
                    cldim = len(cllabels)
                    if plotCorner:
                        fig, axes = plt.subplots(cldim, figsize=(8, cldim/1.5),
                                                sharex=True)
                        for i in range(cldim):
                            ax = axes[i]
                            ax.plot(clsamples[:, :, i].T, "k", alpha=0.3)
                            ax.set_ylabel(cllabels[i])
                            ax.yaxis.set_label_coords(-0.1, 0.5)
                        axes[-1].set_xlabel("step number")
                        
                        if createpdf:
                            pdfname = '%s_pol%i_1.png' % (savetime, idx)
                            plt.savefig(pdfname)
                            plt.close()
                        else:
                            plt.show()
                    
                    if nruns > 300:
                        fl_samples = samples[:, -200:, :].reshape((-1, ndim))
                        fl_clsamples = clsamples[:, -200:, :].reshape((-1, cldim))                
                    elif nruns > 200:
                        fl_samples = samples[:, -100:, :].reshape((-1, ndim))
                        fl_clsamples = clsamples[:, -100:, :].reshape((-1, cldim))   
                    else:
                        fl_samples = samples.reshape((-1, ndim))
                        fl_clsamples = clsamples.reshape((-1, cldim))

                    if plotCorner:
                        ranges = np.percentile(fl_clsamples, [3, 97], axis=0).T
                        fig = corner.corner(fl_clsamples, quantiles=[0.16, 0.5, 0.84],
                                            truths=clmostprop, labels=cllabels)
                        if createpdf:
                            pdfname = '%s_pol%i_2.png' % (savetime, idx)
                            plt.savefig(pdfname)
                            plt.close()
                        else:
                            plt.show()
                        
                    # get the actual fit
                    theta_fit = np.percentile(fl_samples, [50], axis=0).T
                    if bestchi:
                        theta_result = mostprop
                    else:
                        theta_result = theta_fit
                else:
                    theta_result = theta
                    
                results.append(theta_result)
                fit_visamp, fit_visphi, fit_closure = self.calc_vis(theta_result)
                fit_vis2 = fit_visamp**2.
                        
                res_visamp = fit_visamp-visamp
                res_vis2 = fit_vis2-vis2
                res_closure_1 = np.abs(fit_closure-closure)
                res_closure_2 = 360-np.abs(fit_closure-closure)
                check = np.abs(res_closure_1) < np.abs(res_closure_2) 
                res_closure = res_closure_1*check + res_closure_2*(1-check)
                res_visphi_1 = np.abs(fit_visphi-visphi)
                res_visphi_2 = 360-np.abs(fit_visphi-visphi)
                check = np.abs(res_visphi_1) < np.abs(res_visphi_2) 
                res_visphi = res_visphi_1*check + res_visphi_2*(1-check)

                redchi_visamp = np.sum(res_visamp**2./visamp_error**2.*(1-visamp_flag))
                redchi_vis2 = np.sum(res_vis2**2./vis2_error**2.*(1-vis2_flag))
                redchi_closure = np.sum(res_closure**2./closure_error**2.*(1-closure_flag))
                redchi_visphi = np.sum(res_visphi**2./visphi_error**2.*(1-visphi_flag))
                
                
                if redchi2:
                    redchi_visamp /= (visamp.size-np.sum(visamp_flag)-ndof)
                    redchi_vis2 /= (vis2.size-np.sum(vis2_flag)-ndof)
                    redchi_closure /= (closure.size-np.sum(closure_flag)-ndof)
                    redchi_visphi /= (visphi.size-np.sum(visphi_flag)-ndof)
                    chi2string = 'red. chi2'
                else:
                    chi2string = 'chi2'
                
                redchi = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi]
                if idx == 0:
                    redchi0 = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi]
                elif idx == 1:
                    redchi1 = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi]
                    
                if not bequiet:
                    print('\n')
                    print('ndof: %i' % (vis2.size-np.sum(vis2_flag)-ndof))
                    print(chi2string + " for visamp: %.2f" % redchi_visamp)
                    print(chi2string + " for vis2: %.2f" % redchi_vis2)
                    print(chi2string + " for visphi: %.2f" % redchi_visphi)
                    print(chi2string + " for closure: %.2f" % redchi_closure)
                    print('\n')
                
                if not no_fit:
                    percentiles = np.percentile(fl_clsamples, [16, 50, 84],axis=0).T
                    percentiles[:,0] = percentiles[:,1] - percentiles[:,0] 
                    percentiles[:,2] = percentiles[:,2] - percentiles[:,1] 
                    
                    if not bequiet:
                        print("-----------------------------------")
                        print("Best chi2 result:")
                        for i in range(0, cldim):
                            print("%s = %.3f" % (cllabels[i], clmostprop[i]))
                        print("\n")
                        print("MCMC Result:")
                        for i in range(0, cldim):
                            print("%s = %.3f + %.3f - %.3f" % (cllabels[i],
                                                            percentiles[i,1], 
                                                            percentiles[i,2], 
                                                            percentiles[i,0]))
                        print("-----------------------------------")
                
                if createpdf:
                    pdf.cell(40, 8, txt="", ln=0, align="L", border="B")
                    pdf.cell(40, 8, txt="Best chi2 result", ln=0, align="L", border="LB")
                    pdf.cell(60, 8, txt="MCMC result", ln=1, align="L", border="LB")
                    for i in range(0, cldim):
                        pdf.cell(40, 6, txt="%s" % cllabels[i], 
                                ln=0, align="L", border=0)
                        pdf.cell(40, 6, txt="%.3f" % clmostprop[i], 
                                ln=0, align="C", border="L")
                        pdf.cell(60, 6, txt="%.3f + %.3f - %.3f" % 
                                (percentiles[i,1], percentiles[i,2], percentiles[i,0]),
                                ln=1, align="C", border="L")
                    pdf.ln()
                
                if plotScience:
                    self.plotFit(theta_result, fitdata, idx, createpdf=createpdf)
                if writeresults:
                    txtfile.write("# Polarization %i  \n" % (idx+1))
                    for tdx, t in enumerate(mostprop):
                        txtfile.write(str(t))
                        txtfile.write(', ')
                    for tdx, t in enumerate(redchi):
                        txtfile.write(str(t))
                        if tdx != (len(redchi)-1):
                            txtfile.write(', ')
                        else:
                            txtfile.write('\n')

                    percentiles = np.percentile(fl_samples, [16, 50, 84],axis=0).T
                    percentiles[:,0] = percentiles[:,1] - percentiles[:,0] 
                    percentiles[:,2] = percentiles[:,2] - percentiles[:,1] 
                    
                    for tdx, t in enumerate(percentiles[:,1]):
                        txtfile.write(str(t))
                        txtfile.write(', ')
                    for tdx, t in enumerate(redchi):
                        txtfile.write(str(t))
                        if tdx != (len(redchi)-1):
                            txtfile.write(', ')
                        else:
                            txtfile.write('\n')

                    for tdx, t in enumerate(percentiles[:,0]):
                        if tdx in todel:
                            txtfile.write(str(t*0.0))
                        else:
                            txtfile.write(str(t))
                        if tdx != (len(percentiles[:,1])-1):
                            txtfile.write(', ')
                        else:
                            txtfile.write(', 0, 0, 0, 0, 0 \n')

                    for tdx, t in enumerate(percentiles[:,2]):
                        if tdx in todel:
                            txtfile.write(str(t*0.0))
                        else:
                            txtfile.write(str(t))
                        if tdx != (len(percentiles[:,1])-1):
                            txtfile.write(', ')
                        else:
                            txtfile.write(', 0, 0, 0, 0, 0\n')
                            
            if createpdf:
                pdfimages0 = sorted(glob.glob(savetime + '_pol0*.png'))
                pdfimages1 = sorted(glob.glob(savetime + '_pol1*.png'))
                pdfcout = 0
                if plotCorner:
                    pdf.add_page()
                    pdf.cell(0, 10, txt="Polarization  1", ln=1, align="C", border='B')
                    pdf.ln()
                    cover = Image.open(pdfimages0[0])
                    width, height = cover.size
                    ratio = width/height

                    if ratio > (160/115):
                        wi = 160
                        he = 0
                    else:
                        he = 115
                        wi = 0
                    pdf.image(pdfimages0[0], h=he, w=wi)
                    pdf.image(pdfimages0[1], h=115)
                    
                    if 1 in polnom:
                        pdf.add_page()
                        pdf.cell(0, 10, txt="Polarization  2", ln=1, align="C", border='B')
                        pdf.ln()
                        pdf.image(pdfimages1[0], h=he, w=wi)
                        pdf.image(pdfimages1[1], h=115)
                    pdfcout = 2
                    
                if plotScience:
                    titles = ['Vis Amp', 'Vis 2', 'Closure Phase', 'Visibility Phase']
                    for pa in range(4):
                        if not self.fit_for[pa]:
                            continue
                        pdf.add_page()
                        if 1 in polnom:
                            text = '%s, %s: %.2f (P1), %.2f (P2)' % (titles[pa], chi2string, redchi0[pa], redchi1[pa])
                        else:
                            text = '%s, %s: %.2f' % (titles[pa], chi2string, redchi0[pa])
                        pdf.cell(0, 10, txt=text, ln=1, align="C", border='B')
                        pdf.ln()
                        pdf.image(pdfimages0[pdfcout], w=150)
                        if 1 in polnom:
                            pdf.image(pdfimages1[pdfcout], w=150)
                        pdfcout += 1
                
                if not bequiet:
                    print('Save pdf as %s' % pdffilename)
                pdf.output(pdffilename)
                files = glob.glob(savetime + '_pol?_?.png')
                for file in files:
                    os.remove(file)
        if writeresults:
            txtfile.close()
        try:
            fitted = 1-(np.array(self.fit_for)==0)
            redchi0_f = np.sum(redchi0*fitted)
            if onlypol == 0:
                redchi1 = np.zeros_like(redchi0)
            redchi1_f = np.sum(redchi1*fitted)
            redchi_f = redchi0_f + redchi1_f
            if not bequiet:
                print('Combined %s of fitted data: %.3f' % (chi2string, redchi_f))
        except UnboundLocalError:
            pass
        if onlypol is not None and ndit == 1:
            return theta_result
        else:
            return results
        
        
        
    def plotFit(self, theta, fitdata, idx=0, createpdf=False):
        """
        Calculates the theoretical interferometric data for the given parameters in theta
        and plots them together with the data in fitdata.
        Mainly used in fitBinary as result plots.
        """
        rad2as = 180 / np.pi * 3600
        stname = self.name.find('GRAVI')
        title_name = self.name[stname:-5]
        
        (visamp, visamp_error, visamp_flag, 
         vis2, vis2_error, vis2_flag, 
         closure, closure_error, closure_flag, 
         visphi, visphi_error, visphi_flag) = fitdata

        wave = self.wlSC
        dlambda = self.dlambda
        if self.phasemaps:
            wave_model = wave
        else:
            wave_model = np.linspace(wave[0],wave[len(wave)-1],1000)
        dlambda_model = np.zeros((6,len(wave_model)))
        for i in range(0,6):
            dlambda_model[i,:] = np.interp(wave_model, wave, dlambda[i,:])
            
        # Fit
        u = self.u
        v = self.v
        magu = np.sqrt(u**2.+v**2.)
        self.wave = wave_model
        self.dlambda = dlambda_model
        (model_visamp_full, model_visphi_full, 
        model_closure_full)  = self.calc_vis(theta)
        self.wave = wave
        self.dlambda = dlambda
        
        model_vis2_full = model_visamp_full**2.
        
        magu_as = self.spFrequAS
        
        u_as_model = np.zeros((len(u),len(wave_model)))
        v_as_model = np.zeros((len(v),len(wave_model)))
        for i in range(0,len(u)):
            u_as_model[i,:] = u[i]/(wave_model*1.e-6) / rad2as
            v_as_model[i,:] = v[i]/(wave_model*1.e-6) / rad2as
        magu_as_model = np.sqrt(u_as_model**2.+v_as_model**2.)
        

            
        # Visamp 
        if self.fit_for[0]:
            for i in range(0,6):
                plt.errorbar(magu_as[i,:], visamp[i,:]*(1-visamp_flag)[i],
                            visamp_error[i,:]*(1-visamp_flag)[i],
                            color=self.colors_baseline[i],ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(magu_as[i,:], visamp[i,:]*(1-visamp_flag)[i],
                            color=self.colors_baseline[i], alpha=0.5, label=self.baseline_labels[i])
                plt.plot(magu_as_model[i,:], model_visamp_full[i,:],
                        color='k', zorder=100)
            plt.ylabel('visibility modulus')
            plt.ylim(-0.1,1.1)
            plt.xlabel('spatial frequency (1/arcsec)')
            plt.legend()
            if createpdf:
                savetime = self.savetime
                plt.title('Polarization %i' % (idx + 1))
                pdfname = '%s_pol%i_5.png' % (savetime, idx)
                plt.savefig(pdfname)
                plt.close()
            else:
                plt.title(title_name)
                plt.show()
        
        # Vis2
        if self.fit_for[1]:
            for i in range(0,6):
                plt.errorbar(magu_as[i,:], vis2[i,:]*(1-vis2_flag)[i], 
                            vis2_error[i,:]*(1-vis2_flag)[i], 
                            color=self.colors_baseline[i],ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(magu_as[i,:], vis2[i,:]*(1-vis2_flag)[i],
                            color=self.colors_baseline[i],alpha=0.5, label=self.baseline_labels[i])
                plt.plot(magu_as_model[i,:], model_vis2_full[i,:],
                        color='k', zorder=100)
            plt.xlabel('spatial frequency (1/arcsec)')
            plt.ylabel('visibility squared')
            plt.ylim(-0.1,1.1)
            plt.legend()
            if createpdf:
                plt.title('Polarization %i' % (idx + 1))
                pdfname = '%s_pol%i_6.png' % (savetime, idx)
                plt.savefig(pdfname)
                plt.close()
            else:
                plt.title(title_name)
                plt.show()
        
        # T3
        if self.fit_for[2]:
            for i in range(0,4):
                max_u_as_model = self.max_spf[i]/(wave_model*1.e-6) / rad2as
                plt.errorbar(self.spFrequAS_T3[i,:], closure[i,:]*(1-closure_flag)[i],
                            closure_error[i,:]*(1-closure_flag)[i],
                            color=self.colors_closure[i],ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(self.spFrequAS_T3[i,:], closure[i,:]*(1-closure_flag)[i],
                            color=self.colors_closure[i], alpha=0.5, label=self.closure_labels[i])
                plt.plot(max_u_as_model, model_closure_full[i,:], 
                        color='k', zorder=100)
            plt.xlabel('spatial frequency of largest baseline in triangle (1/arcsec)')
            plt.ylabel('closure phase (deg)')
            plt.ylim(-180,180)
            plt.legend()
            if createpdf:
                plt.title('Polarization %i' % (idx + 1))
                pdfname = '%s_pol%i_7.png' % (savetime, idx)
                plt.savefig(pdfname)
                plt.close()
            else:
                plt.title(title_name)
                plt.show()
        
        # VisPhi
        if self.fit_for[3]:
            for i in range(0,6):
                plt.errorbar(magu_as[i,:], visphi[i,:]*(1-visphi_flag)[i], 
                            visphi_error[i,:]*(1-visphi_flag)[i],
                            color=self.colors_baseline[i], ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(magu_as[i,:], visphi[i,:]*(1-visphi_flag)[i],
                            color=self.colors_baseline[i], alpha=0.5, label=self.baseline_labels[i])
                plt.plot(magu_as_model[i,:], model_visphi_full[i,:],
                        color='k', zorder=100)
            plt.ylabel('visibility phase')
            plt.xlabel('spatial frequency (1/arcsec)')
            plt.legend()
            if createpdf:
                plt.title('Polarization %i' % (idx + 1))
                pdfname = '%s_pol%i_8.png' % (savetime, idx)
                plt.savefig(pdfname)
                plt.close()
            else:
                plt.title(title_name)
                plt.show()

