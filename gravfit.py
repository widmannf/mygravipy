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





class GravFit(GravData):
    """
    Class which has all the fitting capabilities for:
    unary, binary and triple fit
    """




    ############################################    
    ############################################
    ################ Phase Maps ################
    ############################################
    ############################################
    
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
            

            #pool = multiprocessing.Pool(nthreads)
            #res = np.array(pool.map(multi_pm, wave))
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
        
        if self.simulate_pm:
            dra = np.zeros_like(np.array(dra))
            ddec = np.zeros_like(np.array(ddec))
            northangle = np.zeros_like(np.array(northangle))
            
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
        

    
    def readPhasemapsSingle(self, ra, dec, northangle, dra, ddec, tel, lam, interp=True):
        """
        Same as readPhasemaps, but only for a single point, for testing
        """
        try:
           self.amp_map
        except AttributeError:
            self.loadPhasemaps(interp=True)

        pos = np.array([ra + dra, dec + ddec])
        if self.tel == 'AT':
            pos /= 4.4

        pos_rot = np.dot(self.rotation(northangle), pos)+100

        wave = self.wlSC
        wdx = find_nearest(wave, lam)
        
        print('Wavelength: %.2f given, will ues %.2f' % (lam, wave[wdx]))
        
        if interp:
            cor_amp = self.amp_map_int[wdx, tel](pos_rot[0], pos_rot[1])
            cor_pha = self.pha_map_int[wdx, tel](pos_rot[0], pos_rot[1])
            cor_int_denom = self.amp_map_denom_int[wdx, tel](pos_rot[0], pos_rot[1])
        else:
            pos_int = np.round(pos_rot).astype(int)
            cor_amp = self.amp_map[wdx,tel][pos_int[0],pos_int[1]]
            cor_pha = self.pha_map[wdx,tel][pos_int[0],pos_int[1]]
            cor_int_denom = self.amp_map_denom[wdx,tel][pos_int[0],pos_int[1]]
        print(cor_amp, cor_pha)
        return cor_amp, cor_pha, cor_int_denom
        
    
    
 
    
    ############################################    
    ############################################
    ############### Binary model ###############
    ############################################
    ############################################

    def vis_intensity_approx(self, s, alpha, lambda0, dlambda):
        """
        Approximation for Modulated interferometric intensity
        s:      B*skypos-opd1-opd2
        alpha:  power law index
        lambda0:zentral wavelength
        dlambda:size of channels 
        """
        x = 2*s*dlambda/lambda0**2.
        #sinc = np.sinc(x/np.pi)  # be aware that np.sinc = np.sin(pi*x)/(pi*x)
        #print('wrong approx!')
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
    
    
    
    def simulateVisdata_single(self, theta, wave, dlambda, u, v, 
                               fixedBG=True, fixedBH=True, 
                               phasemaps=False, phasemapsstuff=None,
                               interppm=True, approx="approx"):
        '''
        Test function to generate a single datapoint for a given u, v, lambda, dlamba & theta
        
        Theta should be a list of:
        dRA, dDEC, f, alpha flare, f BG, (alpha BG), PC RA, PC DEC
        
        Values in bracket are by default not used, can be activated by options:
        fixedBH:        Keep primary power law [False]        
        fixedBG:        Keep background power law [True]  
        
        if phasemaps=True, phasemapsstuff must be a list of:
            [tel1, dra1, ddec1, north_angle1, tel2, dra2, ddec2, north_angle2]
        '''
        
        theta_names_raw = np.array(["dRA", "dDEC", "f", "alpha flare", "f BG", 
                                    "alpha BG", "PC RA", "PC DEC"])
        rad2as = 180 / np.pi * 3600
        # check Theta
        try:
            if len(theta) != 8:
                print('Theta has to include the following 8 parameter:')
                print(theta_names_raw)
                raise ValueError('Wrong number of input parameter given (should be 8)')
        except(TypeError):
            print('Thetha has to include the following 8 parameter:')
            print(theta_names_raw)
            raise ValueError('Wrong number of input parameter given (should be 8)') 
        
        mas2rad = 1e-3 / 3600 / 180 * np.pi
        dRA = theta[0]
        dDEC = theta[1]
        f = theta[2]
        if fixedBH:
            alpha_SgrA = -0.5
        else:
            alpha_SgrA = theta[3]
        fluxRatioBG = theta[4]
        if fixedBG:
            alpha_bg = 3.
        else:
            alpha_bg = theta[5]
        phaseCenterRA = theta[6]
        phaseCenterDEC = theta[7]
        alpha_S2 = 3
        f = 10.**f

        s_SgrA = ((phaseCenterRA)*u + (phaseCenterDEC)*v) * mas2rad * 1e6
        s_S2 = ((dRA+phaseCenterRA)*u + (dDEC+phaseCenterDEC)*v) * mas2rad * 1e6
                
        if phasemaps:
            (tel1, dra1, ddec1, north_angle1, 
             tel2, dra2, ddec2, north_angle2) = phasemapsstuff

            cor_amp_sgr1, cor_pha_sgr1, cor_int_sgr1 = self.readPhasemapsSingle(phaseCenterRA,
                                                                  phaseCenterDEC,
                                                                  north_angle1, 
                                                                  dra1, ddec1,
                                                                  tel1, wave,
                                                                  interp=interppm)
            cor_amp_sgr2, cor_pha_sgr2, cor_int_sgr2 = self.readPhasemapsSingle(phaseCenterRA,
                                                                  phaseCenterDEC,
                                                                  north_angle2, 
                                                                  dra2, ddec2,
                                                                  tel2, wave,
                                                                  interp=interppm)
            cor_amp_s21, cor_pha_s21, cor_int_s21 = self.readPhasemapsSingle(dRA+phaseCenterRA, 
                                                               dDEC+phaseCenterDEC,
                                                               north_angle1, 
                                                               dra1, ddec1,
                                                               tel1, wave,
                                                               interp=interppm)
            cor_amp_s22, cor_pha_s22, cor_int_s22 = self.readPhasemapsSingle(dRA+phaseCenterRA, 
                                                               dDEC+phaseCenterDEC,
                                                               north_angle2, 
                                                               dra2, ddec2,
                                                               tel2, wave,
                                                               interp=interppm)
            # differential opd
            opd_sgr = (cor_pha_sgr1-cor_pha_sgr2)/360*wave
            s_SgrA -= opd_sgr
            opd_s2 = (cor_pha_s21-cor_pha_s22)/360*wave
            s_S2 -= opd_s2
            
            # different coupling
            cr1 = (cor_amp_s21 / cor_amp_sgr1)**2
            cr2 = (cor_amp_s22 / cor_amp_sgr2)**2
            cr_denom1 = (cor_int_s21 / cor_int_sgr1)
            cr_denom2 = (cor_int_s22/ cor_int_sgr2)

            if approx == "approx":
                intSgrA = self.vis_intensity_approx(s_SgrA, alpha_SgrA, wave, dlambda)
                intS2 = self.vis_intensity_approx(s_S2, alpha_S2, wave, dlambda)
                intSgrA_center = self.vis_intensity_approx(0, alpha_SgrA, wave, dlambda)
                intS2_center = self.vis_intensity_approx(0, alpha_S2, wave, dlambda)
                intBG = self.vis_intensity_approx(0, alpha_bg, wave, dlambda)
            elif approx == "analytic":
                intSgrA = self.vis_intensity(s_SgrA, alpha_SgrA, wave, dlambda)
                intS2 = self.vis_intensity(s_S2, alpha_S2, wave, dlambda)
                intSgrA_center = self.vis_intensity(0, alpha_SgrA, wave, dlambda)
                intS2_center = self.vis_intensity(0, alpha_S2, wave, dlambda)
                intBG = self.vis_intensity(0, alpha_bg, wave, dlambda)
            elif approx == "numeric":
                intSgrA = self.vis_intensity_num(s_SgrA, alpha_SgrA, wave, dlambda)
                intS2 = self.vis_intensity_num(s_S2, alpha_S2, wave, dlambda)
                intSgrA_center = self.vis_intensity_num(0, alpha_SgrA, wave, dlambda)
                intS2_center = self.vis_intensity_num(0, alpha_S2, wave, dlambda)
                intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda)
            else:
                raise ValueError("approx needs to be in [approx, analytic, numeric]")
            
            vis = ((intSgrA + f*np.sqrt(cr1*cr2)*intS2)/
                   (np.sqrt(intSgrA_center + f*cr_denom1*intS2_center + fluxRatioBG * intBG)*
                    np.sqrt(intSgrA_center + f*cr_denom2*intS2_center + fluxRatioBG * intBG)))
            
        else:
            if approx == "approx":
                intSgrA = self.vis_intensity_approx(s_SgrA, alpha_SgrA, wave, dlambda)
                intS2 = self.vis_intensity_approx(s_S2, alpha_S2, wave, dlambda)
                intSgrA_center = self.vis_intensity_approx(0, alpha_SgrA, wave, dlambda)
                intS2_center = self.vis_intensity_approx(0, alpha_S2, wave, dlambda)
                intBG = self.vis_intensity_approx(0, alpha_bg, wave, dlambda)
            elif approx == "analytic":
                intSgrA = self.vis_intensity(s_SgrA, alpha_SgrA, wave, dlambda)
                intS2 = self.vis_intensity(s_S2, alpha_S2, wave, dlambda)
                intSgrA_center = self.vis_intensity(0, alpha_SgrA, wave, dlambda)
                intS2_center = self.vis_intensity(0, alpha_S2, wave, dlambda)
                intBG = self.vis_intensity(0, alpha_bg, wave, dlambda)
            elif approx == "numeric":
                intSgrA = self.vis_intensity_num(s_SgrA, alpha_SgrA, wave, dlambda)
                intS2 = self.vis_intensity_num(s_S2, alpha_S2, wave, dlambda)
                intSgrA_center = self.vis_intensity_num(0, alpha_SgrA, wave, dlambda)
                intS2_center = self.vis_intensity_num(0, alpha_S2, wave, dlambda)
                intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda)
            else:
                raise ValueError('apprix has to be approx, analytic or numeric')
                
            
            vis = ((intSgrA + f * intS2)/
                    (intSgrA_center + f * intS2_center + fluxRatioBG * intBG))
            
        return vis
    
    
    def simulateVisdata(self, theta, constant_f=True, use_opds=False, fixedBG=True, fixedBH=True, fiberOff=None, 
                        plot=True, phasemaps=False, phasemapsstuff=None, interppm=True,
                        approx='numeric', source2alpha=None):
        """
        Test function to see how a given theta would look like
        Theta should be a list of:
        dRA, dDEC, f1, (f2), (f3), (f4), alpha flare, f BG, (alpha BG), 
        PC RA, PC DEC, (OPD1), (OPD2), (OPD3), (OPD4)
        
        Values in bracket are by default not used, can be activated by options:
        constant_f:     Constant coupling [True]
        use_opds:       Fit OPDs [False] 
        fixedBG:        Keep background power law [True]

        if phasemaps=True, phasemapsstuff must be a list of:
            [dra, ddec, north_angle]
        
        """
        theta_names_raw = np.array(["dRA", "dDEC", "f1", "f2", "f3", "f4" , "alpha flare",
                                    "f BG", "alpha BG", "PC RA", "PC DEC", "OPD1", "OPD2",
                                    "OPD3", "OPD4"])
        rad2as = 180 / np.pi * 3600
        try:
            if len(theta) != 15:
                print('Theta has to include the following 16 parameter:')
                print(theta_names_raw)
                raise ValueError('Wrong number of input parameter given (should be 16)')
        except(TypeError):
            print('Thetha has to include the following 16 parameter:')
            print(theta_names_raw)
            raise ValueError('Wrong number of input parameter given (should be 16)')            
        
        self.constant_f = constant_f
        self.fixedBG = fixedBG
        self.use_opds = use_opds
        self.fixedBH = fixedBH
        self.interppm = interppm
        self.approx = approx
        self.fixpos = False
        self.specialfit = False
        
        if source2alpha is not None:
            self.source2alpha = source2alpha
        
        if fiberOff is None:
            self.fiberOffX = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFX"] 
            self.fiberOffY = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFY"] 
        else:
            self.fiberOffX = fiberOff[0]
            self.fiberOffY = fiberOff[1]
        if self.verbose:
            print("fiber center: %.2f, %.2f (mas)" % (self.fiberOffX, self.fiberOffY))
        
        if phasemaps:
            self.dra = phasemapsstuff[0]*np.ones(4)
            self.ddec = phasemapsstuff[1]*np.ones(4)
            self.northangle = phasemapsstuff[2]*np.ones(4)
            self.phasemaps = True
        else:
            self.phasemaps = False

        nwave = self.channel
        if nwave != 14:
            raise ValueError('Only usable for 14 channels')
        self.getIntdata(plot=False, flag=False)
        u = self.u
        v = self.v
        wave = self.wlSC_P1
        
        self.getDlambda()
        dlambda = self.dlambda
        
        theta[2:6] = np.log10(theta[2:6])

        visamp, visphi, closure, closamp = self.calc_vis(theta, u, v, wave, dlambda)
        vis2 = visamp**2
        
        if plot:
            gs = gridspec.GridSpec(2,2)
            plt.figure(figsize=(25,25))
            
            if phasemaps:
                wave_model = wave
            else:
                wave_model = np.linspace(wave[0],wave[len(wave)-1],1000)
            dlambda_model = np.zeros((6,len(wave_model)))
            for i in range(0,6):
                dlambda_model[i,:] = np.interp(wave_model, wave, dlambda[i,:])
            (model_visamp_full, model_visphi_full, 
            model_closure_full, model_closamp_full)  = self.calc_vis(theta, u, v, wave_model, dlambda_model)
            model_vis2_full = model_visamp_full**2.

            magu_as = self.spFrequAS
            u_as_model = np.zeros((len(u),len(wave_model)))
            v_as_model = np.zeros((len(v),len(wave_model)))
            for i in range(0,len(u)):
                u_as_model[i,:] = u[i]/(wave_model*1.e-6) / rad2as
                v_as_model[i,:] = v[i]/(wave_model*1.e-6) / rad2as
            magu_as_model = np.sqrt(u_as_model**2.+v_as_model**2.)

            # Visamp 
            axis = plt.subplot(gs[0,0])
            for i in range(0,6):
                plt.plot(magu_as[i,:], visamp[i,:], color=self.colors_baseline[i], ls='', marker='o')
                plt.plot(magu_as_model[i,:], model_visamp_full[i,:], color=self.colors_baseline[i])
            plt.ylabel('VisAmp')
            plt.axhline(1, ls='--', lw=0.5)
            plt.ylim(-0.0,1.1)
            #plt.xlabel('spatial frequency (1/arcsec)')
            
            # Vis2
            axis = plt.subplot(gs[0,1])
            for i in range(0,6):
                plt.errorbar(magu_as[i,:], vis2[i,:], color=self.colors_baseline[i], ls='', marker='o')
                plt.plot(magu_as_model[i,:], model_vis2_full[i,:],
                        color=self.colors_baseline[i], alpha=1.0)
            #plt.xlabel('spatial frequency (1/arcsec)')
            plt.ylabel('V2')
            plt.axhline(1, ls='--', lw=0.5)
            plt.ylim(-0.0,1.1)
            
            # T3
            axis = plt.subplot(gs[1,0])
            maxval = []
            for i in range(0,4):
                max_u_as_model = self.max_spf[i]/(wave_model*1.e-6) / rad2as
                plt.errorbar(self.spFrequAS_T3[i,:], closure[i,:],
                             color=self.colors_closure[i],ls='', marker='o')
                plt.plot(max_u_as_model, model_closure_full[i,:],
                         color=self.colors_closure[i])
            plt.axhline(0, ls='--', lw=0.5)
            plt.xlabel('spatial frequency (1/arcsec)')
            plt.ylabel('T3Phi (deg)')
            maxval = np.max(np.abs(model_closure_full))
            if maxval < 15:
                maxplot=20
            elif maxval < 75:
                maxplot=80
            else:
                maxplot=180
            plt.ylim(-maxplot, maxplot)
            
            # VisPhi
            axis = plt.subplot(gs[1,1])
            for i in range(0,6):
                plt.errorbar(magu_as[i,:], visphi[i,:], color=self.colors_baseline[i], ls='', marker='o')
                plt.plot(magu_as_model[i,:], model_visphi_full[i,:], color=self.colors_baseline[i],alpha=1.0)
            plt.ylabel('VisPhi')
            plt.xlabel('spatial frequency (1/arcsec)')
            plt.axhline(0, ls='--', lw=0.5)
            maxval = np.max(np.abs(model_visphi_full))
            if maxval < 45:
                maxplot=50
            elif maxval < 95:
                maxplot=100
            else:
                maxplot=180
            plt.ylim(-maxplot, maxplot)
            
            plt.suptitle('dRa=%.1f, dDec=%.1f, fratio=%.1f, a=%.1f, fBG=%.1f, PCRa=%.1f, PCDec=%.1f' 
                         % (theta[0], theta[1], theta[2], theta[6], theta[8], theta[10], theta[11]), fontsize=12)
            plt.show()

        return visamp, vis2, visphi, closure, closamp


    def calc_vis(self, theta, u, v, wave, dlambda):
        mas2rad = 1e-3 / 3600 / 180 * np.pi
        rad2mas = 180 / np.pi * 3600 * 1e3
        constant_f = self.constant_f
        fixedBG = self.fixedBG
        use_opds = self.use_opds
        fiberOffX = self.fiberOffX
        fiberOffY = self.fiberOffY
        fixpos = self.fixpos
        fixedBH = self.fixedBH
        specialfit = self.specialfit
        interppm = self.interppm
        approx = self.approx

        phasemaps = self.phasemaps
        if phasemaps:
            northangle = self.northangle
            ddec = self.ddec
            dra = self.dra
        
        if fixpos:
            dRA = self.fiberOffX
            dDEC = self.fiberOffY
        else:
            dRA = theta[0]
            dDEC = theta[1]
        if constant_f:
            fluxRatio = theta[2]
        else:
            fluxRatio1 = theta[2]
            fluxRatio2 = theta[3]
            fluxRatio3 = theta[4]
            fluxRatio4 = theta[5]
        if fixedBH:
            alpha_SgrA = -0.5
        else:
            alpha_SgrA = theta[6]
        fluxRatioBG = theta[7]
        if fixedBG:
            alpha_bg = 3.
        else:
            alpha_bg = theta[8]
        phaseCenterRA = theta[9]
        phaseCenterDEC = theta[10]

        if use_opds:
            opd1 = theta[11]
            opd2 = theta[12]
            opd3 = theta[13]
            opd4 = theta[14]
            opd_bl = np.array([[opd4, opd3],
                               [opd4, opd2],
                               [opd4, opd1],
                               [opd3, opd2],
                               [opd3, opd1],
                               [opd2, opd1]])
        if specialfit:
            special_par = theta[16] 
            sp_bl = np.ones(6)*special_par
            sp_bl *= self.specialfit_bl
        
        try:
            alpha_S2 = self.source2alpha
            if self.verbose:
                print('Alpha of second star is %.2f' % alpha_S2)
        except:
            alpha_S2 = 3
        
        # Flux Ratios
        if constant_f:
            f = np.ones(4)*fluxRatio
        else:
            f = np.array([fluxRatio1, fluxRatio2, fluxRatio3, fluxRatio4])
        f = 10.**f
        f_bl = np.array([[f[3],f[2]],
                         [f[3],f[1]],
                         [f[3],f[0]],
                         [f[2],f[1]],
                         [f[2],f[0]],
                         [f[1],f[0]]])
        
        
        if phasemaps:
            cor_amp_sgr, cor_pha_sgr, cor_int_sgr = self.readPhasemaps(phaseCenterRA,
                                                                       phaseCenterDEC,
                                                                       fromFits=False, 
                                                                       northangle=northangle,
                                                                       dra=dra, ddec=ddec,
                                                                       interp=interppm)
            cor_amp_s2, cor_pha_s2, cor_int_s2 = self.readPhasemaps(dRA+phaseCenterRA, 
                                                                    dDEC+phaseCenterDEC,
                                                                    fromFits=False, 
                                                                    northangle=northangle,
                                                                    dra=dra, ddec=ddec,
                                                                    interp=interppm)

            pm_amp_sgr = np.array([[cor_amp_sgr[0], cor_amp_sgr[1]],
                                    [cor_amp_sgr[0], cor_amp_sgr[2]],
                                    [cor_amp_sgr[0], cor_amp_sgr[3]],
                                    [cor_amp_sgr[1], cor_amp_sgr[2]],
                                    [cor_amp_sgr[1], cor_amp_sgr[3]],
                                    [cor_amp_sgr[2], cor_amp_sgr[3]]])
            pm_pha_sgr = np.array([[cor_pha_sgr[0], cor_pha_sgr[1]],
                                    [cor_pha_sgr[0], cor_pha_sgr[2]],
                                    [cor_pha_sgr[0], cor_pha_sgr[3]],
                                    [cor_pha_sgr[1], cor_pha_sgr[2]],
                                    [cor_pha_sgr[1], cor_pha_sgr[3]],
                                    [cor_pha_sgr[2], cor_pha_sgr[3]]])
            pm_int_sgr = np.array([[cor_int_sgr[0], cor_int_sgr[1]],
                                    [cor_int_sgr[0], cor_int_sgr[2]],
                                    [cor_int_sgr[0], cor_int_sgr[3]],
                                    [cor_int_sgr[1], cor_int_sgr[2]],
                                    [cor_int_sgr[1], cor_int_sgr[3]],
                                    [cor_int_sgr[2], cor_int_sgr[3]]])
            pm_amp_s2 = np.array([[cor_amp_s2[0], cor_amp_s2[1]],
                                    [cor_amp_s2[0], cor_amp_s2[2]],
                                    [cor_amp_s2[0], cor_amp_s2[3]],
                                    [cor_amp_s2[1], cor_amp_s2[2]],
                                    [cor_amp_s2[1], cor_amp_s2[3]],
                                    [cor_amp_s2[2], cor_amp_s2[3]]])
            pm_pha_s2 = np.array([[cor_pha_s2[0], cor_pha_s2[1]],
                                    [cor_pha_s2[0], cor_pha_s2[2]],
                                    [cor_pha_s2[0], cor_pha_s2[3]],
                                    [cor_pha_s2[1], cor_pha_s2[2]],
                                    [cor_pha_s2[1], cor_pha_s2[3]],
                                    [cor_pha_s2[2], cor_pha_s2[3]]])   
            pm_int_s2 = np.array([[cor_int_s2[0], cor_int_s2[1]],
                                    [cor_int_s2[0], cor_int_s2[2]],
                                    [cor_int_s2[0], cor_int_s2[3]],
                                    [cor_int_s2[1], cor_int_s2[2]],
                                    [cor_int_s2[1], cor_int_s2[3]],
                                    [cor_int_s2[2], cor_int_s2[3]]])   
            vis = np.zeros((6,len(wave))) + 0j
            for i in range(0,6):
                try:
                    if self.fit_for[3] == 0:
                        phaseCenterRA = 0
                        phaseCenterDEC = 0
                except AttributeError:
                    pass
                s_SgrA = ((phaseCenterRA)*u[i] + (phaseCenterDEC)*v[i]) * mas2rad * 1e6
                s_S2 = ((dRA+phaseCenterRA)*u[i] + (dDEC+phaseCenterDEC)*v[i]) * mas2rad * 1e6

                if use_opds:
                    s_S2 = s_S2 + opd_bl[i,0] - opd_bl[i,1]
                if specialfit:
                    s_SgrA += sp_bl[i]
                    s_S2 += sp_bl[i]
                
                opd_sgr = (pm_pha_sgr[i,0] - pm_pha_sgr[i,1])/360*wave
                opd_s2 = (pm_pha_s2[i,0] - pm_pha_s2[i,1])/360*wave
                s_SgrA -= opd_sgr
                s_S2 -= opd_s2
                
                cr1 = (pm_amp_s2[i,0] / pm_amp_sgr[i,0])**2
                cr2 = (pm_amp_s2[i,1] / pm_amp_sgr[i,1])**2
                
                cr_denom1 = (pm_int_s2[i,0] / pm_int_sgr[i,0])
                cr_denom2 = (pm_int_s2[i,1] / pm_int_sgr[i,1])
                
                if approx == "approx":
                    intSgrA = self.vis_intensity_approx(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intS2 = self.vis_intensity_approx(s_S2, alpha_S2, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_approx(0, alpha_SgrA, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_approx(0, alpha_S2, wave, dlambda[i,:])
                    intBG = self.vis_intensity_approx(0, alpha_bg, wave, dlambda[i,:])
                elif approx == "analytic":
                    intSgrA = self.vis_intensity(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intS2 = self.vis_intensity(s_S2, alpha_S2, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity(0, alpha_SgrA, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity(0, alpha_S2, wave, dlambda[i,:])
                    intBG = self.vis_intensity(0, alpha_bg, wave, dlambda[i,:])
                elif approx == "numeric":
                    intSgrA = self.vis_intensity_num(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intS2 = self.vis_intensity_num(s_S2, alpha_S2, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_num(0, alpha_SgrA, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_num(0, alpha_S2, wave, dlambda[i,:])
                    intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda[i,:])
                else:
                    raise ValueError('approx has to be approx, analytic or numeric')
                
                vis[i,:] = ((intSgrA + 
                            np.sqrt(f_bl[i,0] * f_bl[i,1] * cr1 * cr2) * intS2)/
                            (np.sqrt(intSgrA_center + f_bl[i,0] * cr_denom1 * intS2_center 
                                    + fluxRatioBG * intBG) *
                             np.sqrt(intSgrA_center + f_bl[i,1] * cr_denom2 * intS2_center 
                                    + fluxRatioBG * intBG)))  

        else:
            vis = np.zeros((6,len(wave))) + 0j
            for i in range(0,6):
                try:
                    if self.fit_for[3] == 0:
                        phaseCenterRA = 0
                        phaseCenterDEC = 0
                except AttributeError:
                    pass
                s_SgrA = ((phaseCenterRA)*u[i] + (phaseCenterDEC)*v[i]) * mas2rad * 1e6
                s_S2 = ((dRA+phaseCenterRA)*u[i] + (dDEC+phaseCenterDEC)*v[i]) * mas2rad * 1e6

                if use_opds:
                    s_S2 = s_S2 + opd_bl[i,0] - opd_bl[i,1]
                if specialfit:
                    s_SgrA += sp_bl[i]
                    s_S2 += sp_bl[i]
                
                if approx == "approx":
                    intSgrA = self.vis_intensity_approx(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intS2 = self.vis_intensity_approx(s_S2, alpha_S2, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_approx(0, alpha_SgrA, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_approx(0, alpha_S2, wave, dlambda[i,:])
                    intBG = self.vis_intensity_approx(0, alpha_bg, wave, dlambda[i,:])
                elif approx == "analytic":
                    intSgrA = self.vis_intensity(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intS2 = self.vis_intensity(s_S2, alpha_S2, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity(0, alpha_SgrA, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity(0, alpha_S2, wave, dlambda[i,:])
                    intBG = self.vis_intensity(0, alpha_bg, wave, dlambda[i,:])
                elif approx == "numeric":
                    intSgrA = self.vis_intensity_num(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intS2 = self.vis_intensity_num(s_S2, alpha_S2, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_num(0, alpha_SgrA, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_num(0, alpha_S2, wave, dlambda[i,:])
                    intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda[i,:])
                else:
                    raise ValueError('approx has to be approx, analytic or numeric')

                vis[i,:] = ((intSgrA + np.sqrt(f_bl[i,0] * f_bl[i,1]) * intS2)/
                            (np.sqrt(intSgrA_center + f_bl[i,0] * intS2_center 
                                    + fluxRatioBG * intBG) *
                            np.sqrt(intSgrA_center + f_bl[i,1] * intS2_center 
                                    + fluxRatioBG * intBG)))

        visamp = np.abs(vis)
        visphi = np.angle(vis, deg=True)
        closure = np.zeros((4, len(wave)))
        closamp = np.zeros((4, len(wave)))
        for idx in range(4):
            closure[idx] = visphi[self.bispec_ind[idx,0]] + visphi[self.bispec_ind[idx,1]] - visphi[self.bispec_ind[idx,2]]
            closamp[idx] = visamp[self.bispec_ind[idx,0]] * visamp[self.bispec_ind[idx,1]] * visamp[self.bispec_ind[idx,2]]

        visphi = visphi + 360.*(visphi<-180.) - 360.*(visphi>180.)
        closure = closure + 360.*(closure<-180.) - 360.*(closure>180.)
        return visamp, visphi, closure, closamp
    
    
    def lnprob(self, theta, fitdata, u, v, wave, dlambda, lower, upper):
        if np.any(theta < lower) or np.any(theta > upper):
            return -np.inf
        return self.lnlike(theta, fitdata, u, v, wave, dlambda)
    
        
    def lnlike(self, theta, fitdata, u, v, wave, dlambda):       
        """
        Calculate the likelihood estimation for the MCMC run
        """
        # Model
        model_visamp, model_visphi, model_closure, model_closamp = self.calc_vis(theta,u,v,wave,dlambda)
        model_vis2 = model_visamp**2.
        
        #Data
        (visamp, visamp_error, visamp_flag,
         vis2, vis2_error, vis2_flag,
         closure, closure_error, closure_flag,
         visphi, visphi_error, visphi_flag,
         closamp, closamp_error, closamp_flag) = fitdata
        
        res_visamp = np.sum(-(model_visamp-visamp)**2/visamp_error**2*(1-visamp_flag))
        res_vis2 = np.sum(-(model_vis2-vis2)**2./vis2_error**2.*(1-vis2_flag))
        res_closamp = np.sum(-(model_closamp-closamp)**2/closamp_error**2*(1-closamp_flag))
        
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
                             res_phi * self.fit_for[3] + 
                             res_closamp * self.fit_for[4])
        
        return ln_prob_res 
    
    
    
    def fitBinary(self, 
                  nthreads=4, 
                  nwalkers=500, 
                  nruns=500, 
                  bestchi=True,
                  bequiet=False,
                  fit_for=np.array([0.5,0.5,1.0,0.0,0.0]), 
                  nophases=False, 
                  approx='approx', 
                  donotfit=False, 
                  donotfittheta=None, 
                  onlypol1=False, 
                  initial=None, 
                  dRA=0., 
                  dDEC=0., 
                  dphRA=0.1, 
                  dphDec=0.1,
                  flagtill=3, 
                  flagfrom=13,
                  use_opds=False, 
                  fixedBG=True, 
                  noS2=True, 
                  fixpos=False, 
                  onlypos=False,
                  fixedBH=False, 
                  constant_f=True,
                  specialpar=np.array([0,0,0,0,0,0]), 
                  plot=True, 
                  plotres=True, 
                  createpdf=True, 
                  writeresults=True, 
                  redchi2=False,
                  phasemaps=False,
                  interppm=True,
                  smoothkernel=10,
                  simulate_pm=False,
                  pmdatayear=2019):
        '''
        Binary fit to GRAVITY data
        Parameter:
        nthreads:       number of cores [4] 
        nwalkers:       number of walkers [500] 
        nruns:          number of MCMC runs [500] 
        bestchi:        Gives best chi2 (for True) or mcmc res as output [True]
        bequiet:        Suppresses ALL outputs
        fit_for:        weight of VA, V2, T3, VP, T3AMP [[0.5,0.5,1.0,0.0,0.0]] 
        nophases:       Does not fit phases, but still considers them for chi2
                        (for testing) [False]
        approx:         Kind of integration for visibilities (approx, numeric, analytic)
        donotfit:       Only gives fitting results for parameters from donotfittheta [False]
        donotfittheta:  has to be given for donotfit [None]
        onlypol1:       Only fits polarization 1 for split mode [False]
        initial:        Initial guess for fit [None]
        dRA:            Initial guess for dRA (taken from SOFFX if 0) [0]
        dDEC:           Initial guess for dDEC (taken from SOFFY if 0) [0]
        dphRA:          Initial guess for phase center RA [0]
        dphDec:         Initial guess for phase center DEC [0]
        flagtill:       Flag blue channels, has to be changed for not LOW [3] 
        flagfrom:       Flag red channels, has to be changed for not LOW [13]
        use_opds:       Fit OPDs [False] 
        fixedBG:        Fit for background power law [False]
        noS2:           Does not do anything if OFFX and OFFY=0
        fixpos:         Does nto fit the distance between the sources [False]
        onlypos:        Fixes everything except pos [False]
        fixedBH:        Fit for black hole power law [False]
        constant_f:     Constant coupling [True]
        specialpar:     Allows OPD for individual baseline [0,0,0,0,0,0]
        plot:           plot MCMC results [True]
        plotres:        plot fit result [True]
        createpdf:      Creates a pdf with fit results and all plots [True] 
        writeresults:   Write fit results in file [True]
        redchi2:        Gives redchi2 instead of chi2 [False]
        phasemaps:      Use Phasemaps for fit [False]
        interppm:       Interpolate Phasemaps [True]
        smoothkernel:   Size of smoothing kernel in mas [15]
        simulate_pm:    Phasemaps for simulated data, sets ACQ parameter to 0 [False]
        
        For a fit to two components: A (SgrA*) and B (S2)
        The possible fit properties are:
        dRA         Separation from A to B in RA [mas]
        dDEC        Separation from A to B in Dec [mas]
        f1          Flux ratio log(B/A) for telescope 1 (or all telescopes)
        f2          Flux ratio log(B/A) for telescope 2
        f3          Flux ratio log(B/A) for telescope 3
        f4          Flux ratio log(B/A) for telescope 4
        alpha A     spectral index of component A:  vSv = v^alpha 
        f BG        Flux ratio of background: BG/A
        alpha BG    spectral index of component BG:  vSv = v^alpha 
        PC RA       Offset of the phasecenter from field center in RA [mas]
        PC DEC      Offset of the phasecenter from field center in Dec [mas]
        OPD1        OPD for telescope 1 [mum]
        OPD2        OPD for telescope 2 [mum]
        OPD3        OPD for telescope 3 [mum]
        OPD4        OPD for telescope 4 [mum]
        special     OPD for individual baselines
        '''
        if self.resolution != 'LOW' and flagtill == 3 and flagfrom == 13:
            raise ValueError('Initial values for flagtill and flagfrom have to be changed if not low resolution')
        
        self.fit_for = fit_for
        self.constant_f = constant_f
        self.use_opds = use_opds
        self.fixedBG = fixedBG
        self.fixpos = fixpos
        self.fixedBH = fixedBH
        self.interppm = interppm
        self.approx = approx
        #self.smoothfwhm = smoothfwhm
        self.simulate_pm = simulate_pm
        self.bequiet = bequiet
        self.smoothkernel = smoothkernel
        rad2as = 180 / np.pi * 3600
        
        if np.any(specialpar):
            self.specialfit = True
            self.specialfit_bl = specialpar
            if not bequiet:
                print('Specialfit parameter applied to BLs:')
                nonzero = np.nonzero(self.specialfit_bl)[0]
                print(*list(nonzero*self.specialfit_bl[nonzero]))
                print('\n')
        else:
            self.specialfit = False
        specialfit = self.specialfit
        
        self.phasemaps = phasemaps
        self.datayear = pmdatayear
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


        # Get data from file
        tel = fits.open(self.name)[0].header["TELESCOP"]
        if tel == 'ESO-VLTI-U1234':
            self.tel = 'UT'
        elif tel == 'ESO-VLTI-A1234':
            self.tel = 'AT'
        else:
            raise ValueError('Telescope not AT or UT, something wrong with input data')

        nwave = self.channel

        self.getIntdata(plot=False, flag=False)

        MJD = fits.open(self.name)[0].header["MJD-OBS"]
        u = self.u
        v = self.v
        wave = self.wlSC
        
        try:
            self.fiberOffX = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFX"] 
            self.fiberOffY = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFY"]
        except KeyError:
            self.fiberOffX = 0
            self.fiberOffY = 0
        if not bequiet:
            print("fiber center: %.2f, %.2f (mas)" % (self.fiberOffX,
                                                      self.fiberOffY))
        if dRA == 0 and dDEC == 0:
            if self.fiberOffX != 0 and self.fiberOffY != 0:
                dRA = self.fiberOffX
                dDEC = self.fiberOffY
            if self.fiberOffX == 0 and self.fiberOffY == 0:
                if noS2:
                    if not bequiet:
                        print('No Fiber offset, if you want to fit this file use noS2=False')
                    return 0
            if dRA == 0 and dDEC == 0:
                if not bequiet:
                    print('Fiber offset is zero, guess for dRA & dDEC should be given with function')
        else:
            if not bequiet:
                print('Guess for RA & DEC from function as: %.2f, %.2f' % (dRA, dDEC))
        
        stname = self.name.find('GRAVI')
        if phasemaps:
            txtfilename = 'pm_binaryfit_' + self.name[stname:-5] + '.txt'
        else:
            txtfilename = 'binaryfit_' + self.name[stname:-5] + '.txt'
        if writeresults:
            txtfile = open(txtfilename, 'w')
            txtfile.write('# Results of binary fit for %s \n' % self.name[stname:])
            txtfile.write('# Lines are: Best chi2, MCMC result, MCMC error -, MCMC error + \n')
            txtfile.write('# Rowes are: dRA, dDEC, f1, f2, f3, f4, alpha flare, f BG, alpha BG, PC RA, PC DEC, OPD1, OPD2, OPD3, OPD4 \n')
            txtfile.write('# Parameter which are not fitted have 0.0 as error \n')
            txtfile.write('# MJD: %f \n' % MJD)
            txtfile.write('# OFFX: %f \n' % self.fiberOffX)
            txtfile.write('# OFFY: %f \n\n' % self.fiberOffY)

        self.wave = wave
        self.getDlambda()
        dlambda = self.dlambda
        results = []

        # Initial guesses
        if initial is not None:
            if len(initial) != 16:
                raise ValueError('Length of initial parameter list is not correct')
            size = 4
            dRA_init = np.array([initial[0],initial[0]-size,initial[0]+size])
            dDEC_init = np.array([initial[1],initial[1]-size,initial[1]+size])

            flux_ratio_1_init = np.array([np.log10(initial[2]), np.log10(0.01), np.log10(100.)])
            flux_ratio_2_init = np.array([np.log10(initial[3]), np.log10(0.001), np.log10(100.)])
            flux_ratio_3_init = np.array([np.log10(initial[4]), np.log10(0.001), np.log10(100.)])
            flux_ratio_4_init = np.array([np.log10(initial[5]), np.log10(0.001), np.log10(100.)])

            alpha_SgrA_init = np.array([initial[6],-5.,7.])
            flux_ratio_bg_init = np.array([initial[7],0.,20.])
            color_bg_init = np.array([initial[8],-5.,7.])

            size = 2
            phase_center_RA_init = np.array([initial[9],initial[9]-size,initial[9]+size])
            phase_center_DEC_init = np.array([initial[10],initial[10]-size,initial[10]+size])

            opd_max = 0.5 # maximum opd in microns (lambda/4)
            opd_1_init = [initial[11],-opd_max,opd_max]
            opd_2_init = [initial[12],-opd_max,opd_max]
            opd_3_init = [initial[13],-opd_max,opd_max]
            opd_4_init = [initial[14],-opd_max,opd_max]
            special_par = [initial[15], -2, 2]
        else:
            size = 4
            dRA_init = np.array([dRA,dRA-size,dRA+size])
            dDEC_init = np.array([dDEC,dDEC-size,dDEC+size])

            fr_start = np.log10(0.1)
            flux_ratio_1_init = np.array([fr_start, np.log10(0.01), np.log10(10.)])
            flux_ratio_2_init = np.array([fr_start, np.log10(0.001), np.log10(10.)])
            flux_ratio_3_init = np.array([fr_start, np.log10(0.001), np.log10(10.)])
            flux_ratio_4_init = np.array([fr_start, np.log10(0.001), np.log10(10.)])

            alpha_SgrA_init = np.array([-1.,-10.,10.])
            flux_ratio_bg_init = np.array([0.1,0.,20.])
            color_bg_init = np.array([3.,-10.,10.])

            size = 5
            phase_center_RA = dphRA
            phase_center_DEC = dphDec

            phase_center_RA_init = np.array([phase_center_RA,phase_center_RA-size,phase_center_RA+size])
            phase_center_DEC_init = np.array([phase_center_DEC,phase_center_DEC-size,phase_center_DEC+size])

            opd_max = 0.5 # maximum opd in microns (lambda/4)
            opd_1_init = [0.1,-opd_max,opd_max]
            opd_2_init = [0.1,-opd_max,opd_max]
            opd_3_init = [0.1,-opd_max,opd_max]
            opd_4_init = [0.1,-opd_max,opd_max]
            special_par = [-0.15, -2, 2]
            
        
        # initial fit parameters 
        theta = np.array([dRA_init[0],dDEC_init[0],flux_ratio_1_init[0],flux_ratio_2_init[0],
                          flux_ratio_3_init[0],flux_ratio_4_init[0],alpha_SgrA_init[0],
                          flux_ratio_bg_init[0],color_bg_init[0],phase_center_RA_init[0],
                          phase_center_DEC_init[0],opd_1_init[0],opd_2_init[0],opd_3_init[0],opd_4_init[0],special_par[0]])

        # lower limit on fit parameters 
        theta_lower = np.array([dRA_init[1],dDEC_init[1],flux_ratio_1_init[1],flux_ratio_2_init[1],
                                flux_ratio_3_init[1],flux_ratio_4_init[1],alpha_SgrA_init[1],
                                flux_ratio_bg_init[1],color_bg_init[1],phase_center_RA_init[1],
                                phase_center_DEC_init[1],opd_1_init[1],opd_2_init[1],opd_3_init[1],opd_4_init[1], special_par[1]])

        # upper limit on fit parameters 
        theta_upper = np.array([dRA_init[2],dDEC_init[2],flux_ratio_1_init[2],flux_ratio_2_init[2],
                                flux_ratio_3_init[2],flux_ratio_4_init[2],alpha_SgrA_init[2],
                                flux_ratio_bg_init[2],color_bg_init[2],phase_center_RA_init[2],
                                phase_center_DEC_init[2],opd_1_init[2],opd_2_init[2],opd_3_init[2],opd_4_init[2], special_par[2]])

        theta_names = np.array(["dRA", "dDEC", "f1", "f2", "f3", "f4" , r"$\alpha_{flare}$", r"$f_{bg}$",
                                r"$\alpha_{bg}$", r"$RA_{PC}$", r"$DEC_{PC}$", "OPD1", "OPD2", "OPD3", "OPD4", "special"])
        theta_names_raw = np.array(["dRA", "dDEC", "f1", "f2", "f3", "f4" , "alpha flare", "f BG",
                                    "alpha BG", "PC RA", "PC DEC", "OPD1", "OPD2", "OPD3", "OPD4", "special"])


        ndim = len(theta)
        todel = []
        if onlypos:
            for tdx in range(2,ndim):
                todel.append(tdx)
        else:
            if fixpos:
                todel.append(0)
                todel.append(1)
            if constant_f:
                todel.append(3)
                todel.append(4)
                todel.append(5)
            if fixedBH:
                todel.append(6)
            if fixedBG:
                todel.append(8)
            if fit_for[3] == 0 or nophases:
                todel.append(9)
                todel.append(10)
            if not use_opds:
                todel.append(11)
                todel.append(12)
                todel.append(13)
                todel.append(14)
            if not specialfit:
                todel.append(15)
        ndof = ndim - len(todel)
        
        if donotfit:
            if donotfittheta is None:
                raise ValueError('If donotfit is True, fit values have to be given by donotfittheta')
            if len(donotfittheta) != ndim:
                print(theta_names_raw)
                raise ValueError('donotfittheta has to have %i parameters, see above' % ndim)
            if plot:
                raise ValueError('If donotfit is True, cannot create MCMC plots')
            if writeresults or createpdf:
                raise ValueError('If donotfit is True, writeresults and createpdf should be False')
            print('Will not fit the data, just print out the results for the given theta')
            #donotfittheta[2:6] = np.log10(donotfittheta[2:6])
            
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
            polnom = 2
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
            polnom = 1

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
                        pdffilename = 'pm_binaryfit_' + self.name[stname:-5] + '.pdf'
                    else:
                        pdffilename = 'pm_binaryfit_' + self.name[stname:-5] + '_DIT' + str(dit) + '.pdf'
                else:
                    if ndit == 1:
                        pdffilename = 'binaryfit_' + self.name[stname:-5] + '.pdf'
                    else:
                        pdffilename = 'binaryfit_' + self.name[stname:-5] + '_DIT' + str(dit) + '.pdf'
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
                pdf.cell(40, 6, txt="Constant coulping", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(constant_f), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Fixed Bg", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fixedBG), ln=1, align="L", border=0)
                pdf.cell(40, 6, txt="Flag before/after", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(flagtill) + '/' + str(flagfrom), 
                        ln=1, align="L", border=0)
                pdf.cell(40, 6, txt="Result: Best Chi2", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(bestchi), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Fit OPDs", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(use_opds), ln=1, align="L", border=0)
                pdf.cell(40, 6, txt="Fixpos", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fixpos), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Fixed BH", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fixedBH), ln=1, align="L", border=0)
                pdf.cell(40, 6, txt="Phasemaps", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(phasemaps), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Integral solved by", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=approx, ln=1, align="L", border=0)
                #pdf.cell(40, 6, txt="Phasemaps smoothed", ln=0, align="L", border=0)
                #pdf.cell(40, 6, txt=str(smoothpm), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Smoothing FWHM", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(smoothkernel), ln=1, align="L", border=0)
                pdf.ln()
            
            if not bequiet and not donotfit:
                print('Run MCMC for DIT %i' % (dit+1))
            ditstart = dit*6
            ditstop = ditstart + 6
            t3ditstart = dit*4
            t3ditstop = t3ditstart + 4
            
            if onlypol1:
                polnom = 1
            for idx in range(polnom):
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
                        if par < 2:
                            pos[:,par] = theta[par] + width*np.random.randn(nwalkers)
                        else:
                            pos[:,par] = theta[par] + width*np.random.randn(nwalkers)
                if not bequiet:
                    if not donotfit:
                        print('Run MCMC for Pol %i' % (idx+1))
                    else:
                        print('Pol %i' % (idx+1))
                fitdata = [visamp, visamp_error, visamp_flag,
                            vis2, vis2_error, vis2_flag,
                            closure, closure_error, closure_flag,
                            visphi, visphi_error, visphi_flag,
                            closamp, closamp_error, closamp_flag]
                
                self.fitstuff = [fitdata, u, v, wave, dlambda, theta]
                
                if not donotfit:
                    if nthreads == 1:
                        sampler = emcee.EnsembleSampler(nwalkers, ndim, self.lnprob, 
                                                            args=(fitdata, u, v, wave,
                                                                dlambda, theta_lower,
                                                                theta_upper))
                        if bequiet:
                            sampler.run_mcmc(pos, nruns, progress=False)
                        else:
                            sampler.run_mcmc(pos, nruns, progress=True)
                    else:
                        with Pool(processes=nthreads) as pool:
                            sampler = emcee.EnsembleSampler(nwalkers, ndim, self.lnprob, 
                                                            args=(fitdata, u, v, wave,
                                                                dlambda, theta_lower,
                                                                theta_upper),
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
                    cllabels_raw = np.delete(theta_names_raw, todel)
                    clmostprop = np.delete(mostprop, todel)
                    
                    cldim = len(cllabels)
                    if plot:
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

                    if plot:
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
                    theta_result = donotfittheta
                    
                results.append(theta_result)
                fit_visamp, fit_visphi, fit_closure, fit_closamp = self.calc_vis(theta_result, u, v, wave, dlambda)
                fit_vis2 = fit_visamp**2.
                        
                res_visamp = fit_visamp-visamp
                res_vis2 = fit_vis2-vis2
                res_closamp = fit_closamp-closamp
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
                redchi_closamp = np.sum(res_closamp**2./closamp_error**2.*(1-closamp_flag))
                redchi_visphi = np.sum(res_visphi**2./visphi_error**2.*(1-visphi_flag))
                
                
                if redchi2:
                    redchi_visamp /= (visamp.size-np.sum(visamp_flag)-ndof)
                    redchi_vis2 /= (vis2.size-np.sum(vis2_flag)-ndof)
                    redchi_closure /= (closure.size-np.sum(closure_flag)-ndof)
                    redchi_closamp /= (closamp.size-np.sum(closamp_flag)-ndof)
                    redchi_visphi /= (visphi.size-np.sum(visphi_flag)-ndof)
                    chi2string = 'red. chi2'
                else:
                    chi2string = 'chi2'
                
                redchi = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi, redchi_closamp]
                if idx == 0:
                    redchi0 = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi, redchi_closamp]
                elif idx == 1:
                    redchi1 = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi, redchi_closamp]
                    
                if not bequiet:
                    print('\n')
                    print('ndof: %i' % (vis2.size-np.sum(vis2_flag)-ndof))
                    print(chi2string + " for visamp: %.2f" % redchi_visamp)
                    print(chi2string + " for vis2: %.2f" % redchi_vis2)
                    print(chi2string + " for visphi: %.2f" % redchi_visphi)
                    print(chi2string + " for closure: %.2f" % redchi_closure)
                    print(chi2string + " for closamp: %.2f" % redchi_closamp)
                    print('\n')
                    #print("average visamp error: %.2f" % 
                        #np.mean(visamp_error*(1-visamp_flag)))
                    #print("average vis2 error: %.2f" % 
                        #np.mean(vis2_error*(1-vis2_flag)))
                    #print("average closure error (deg): %.2f" % 
                        #np.mean(closure_error*(1-closure_flag)))
                    #print("average visphi error (deg): %.2f" % 
                        #np.mean(visphi_error*(1-visphi_flag)))
                
                if not donotfit:
                    percentiles = np.percentile(fl_clsamples, [16, 50, 84],axis=0).T
                    percentiles[:,0] = percentiles[:,1] - percentiles[:,0] 
                    percentiles[:,2] = percentiles[:,2] - percentiles[:,1] 
                    
                    if not bequiet:
                        print("-----------------------------------")
                        print("Best chi2 result:")
                        for i in range(0, cldim):
                            print("%s = %.3f" % (cllabels_raw[i], clmostprop[i]))
                        print("\n")
                        print("MCMC Result:")
                        for i in range(0, cldim):
                            print("%s = %.3f + %.3f - %.3f" % (cllabels_raw[i],
                                                            percentiles[i,1], 
                                                            percentiles[i,2], 
                                                            percentiles[i,0]))
                        print("-----------------------------------")
                
                if createpdf:
                    pdf.cell(40, 8, txt="", ln=0, align="L", border="B")
                    pdf.cell(40, 8, txt="Best chi2 result", ln=0, align="L", border="LB")
                    pdf.cell(60, 8, txt="MCMC result", ln=1, align="L", border="LB")
                    for i in range(0, cldim):
                        pdf.cell(40, 6, txt="%s" % cllabels_raw[i], 
                                ln=0, align="L", border=0)
                        pdf.cell(40, 6, txt="%.3f" % clmostprop[i], 
                                ln=0, align="C", border="L")
                        pdf.cell(60, 6, txt="%.3f + %.3f - %.3f" % 
                                (percentiles[i,1], percentiles[i,2], percentiles[i,0]),
                                ln=1, align="C", border="L")
                    pdf.ln()
                
                if plotres:
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
                if plot:
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
                    
                    if polnom == 2:
                        pdf.add_page()
                        pdf.cell(0, 10, txt="Polarization  2", ln=1, align="C", border='B')
                        pdf.ln()
                        pdf.image(pdfimages1[0], h=he, w=wi)
                        pdf.image(pdfimages1[1], h=115)
                    pdfcout = 2
                    
                if plotres:
                    titles = ['Vis Amp', 'Vis 2', 'Closure Phase', 'Visibility Phase', 'Closure Amp']
                    for pa in range(5):
                        if pa == 3 and not self.fit_for[pa]:
                            continue
                        if pa == 4 and not self.fit_for[pa]:
                            continue
                        pdf.add_page()
                        if polnom == 2:
                            text = '%s, %s: %.2f (P1), %.2f (P2)' % (titles[pa], chi2string, redchi0[pa], redchi1[pa])
                        else:
                            text = '%s, %s: %.2f' % (titles[pa], chi2string, redchi0[pa])
                        pdf.cell(0, 10, txt=text, ln=1, align="C", border='B')
                        pdf.ln()
                        pdf.image(pdfimages0[pdfcout+pa], w=150)
                        if polnom == 2:
                            pdf.image(pdfimages1[pdfcout+pa], w=150)
                
                if not bequiet:
                    print('Save pdf as %s' % pdffilename)
                pdf.output(pdffilename)
                files = glob.glob(savetime + '_pol?_?.png')
                for file in files:
                    os.remove(file)
        if writeresults:
            txtfile.close()
        if not bequiet:
            fitted = 1-(np.array(self.fit_for)==0)
            redchi0_f = np.sum(redchi0*fitted)
            if polnom < 2:
                redchi1 = np.zeros_like(redchi0)
            redchi1_f = np.sum(redchi1*fitted)
            redchi_f = redchi0_f + redchi1_f
            print('Combined %s of fitted data: %.3f' % (chi2string, redchi_f))
        if onlypol1 and ndit == 1:
            return theta_result
        else:
            return results
        
        
        
        
        
        
    ##################################################################################
    ##################################################################################
    ## Triple Fit
    ##################################################################################
    ##################################################################################
        
    def lnprob3(self, theta, fitdata, u, v, wave, dlambda, lower, upper):
        if np.any(theta < lower) or np.any(theta > upper):
            return -np.inf
        return self.lnlike3(theta, fitdata, u, v, wave, dlambda)
    
    
    def lnlike3(self, theta, fitdata, u, v, wave, dlambda):       
        """
        Calculate the likelihood estimation for the MCMC run
        """
        # Model
        model_visamp, model_visphi, model_closure, model_closamp = self.calc_vis3(theta,u,v,wave,dlambda)
        model_vis2 = model_visamp**2.
        
        #Data
        (visamp, visamp_error, visamp_flag,
         vis2, vis2_error, vis2_flag,
         closure, closure_error, closure_flag,
         visphi, visphi_error, visphi_flag,
         closamp, closamp_error, closamp_flag) = fitdata
        
        res_visamp = np.sum(-(model_visamp-visamp)**2/visamp_error**2*(1-visamp_flag))
        res_vis2 = np.sum(-(model_vis2-vis2)**2./vis2_error**2.*(1-vis2_flag))
        res_closamp = np.sum(-(model_closamp-closamp)**2/closamp_error**2*(1-closamp_flag))
        
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
                             res_phi * self.fit_for[3] + 
                             res_closamp * self.fit_for[4])
        
        return ln_prob_res 
    
    
    ## SvF 13/11/2020: added parts of the phasemap implementation 
    def calc_vis3(self, theta, u, v, wave, dlambda):
        mas2rad = 1e-3 / 3600 / 180 * np.pi
        rad2mas = 180 / np.pi * 3600 * 1e3
        
        fixedBG = self.fixedBG
        fiberOffX = self.fiberOffX
        fiberOffY = self.fiberOffY
        fixpos = self.fixpos
        fixedBH = self.fixedBH
        approx = self.approx
        fixS29 = self.fixS29
        donotfit = self.donotfit
        interppm = self.interppm
        phasemaps = self.phasemaps
        if phasemaps:
            northangle = self.northangle
            ddec = self.ddec
            dra = self.dra
        if fixpos:
            dRA = self.fiberOffX
            dDEC = self.fiberOffY
        else:
            dRA = theta[0]
            dDEC = theta[1]
        fluxRatio = theta[2]
        f = 10**fluxRatio
        
        dRA2 = theta[3]
        dDEC2 = theta[4]
        if fixS29:
            # Get S29 flux ratio from s2/sgra* flux ratio
            s2_fr = 10**fluxRatio                   # s2/sgra incl. fiber coupling
            if phasemaps:
                f1_fr = 10**(-(18.7-14.1)/2.5)*s2_fr    # s29/sgra w/o fiber coupling
                f2 = f1_fr
            else:
                if donotfit:
                    print('s2/sgra incl. fiber coupling: %.3f' % s2_fr)
                s2_pos = np.array([dRA, dDEC])
                fiber_coup_s2 = np.exp(-1*(2*np.pi*np.sqrt(np.sum(s2_pos**2))/280)**2)
                s2_fr = s2_fr / fiber_coup_s2              # s2/sgra w/o fiber coupling
                if donotfit:
                    print('s2/sgra w/o fiber coupling: %.3f' % s2_fr)
                    
                f1_fr = 10**(-(18.7-14.1)/2.5)*s2_fr    # s29/sgra w/o fiber coupling
                if donotfit:
                    print('s29/sgra w/o fiber coupling: %.3f' % f1_fr)
                    
                f1_pos = np.array([dRA2, dDEC2])
                fiber_coup_f1 = np.exp(-1*(2*np.pi*np.sqrt(np.sum(f1_pos**2))/280)**2)
                f2 = f1_fr * fiber_coup_f1         # s29/sgra incl. fiber coupling
                if donotfit:
                    print('s29/sgra incl. fiber coupling: %.3f' % f2)
        else:
            fluxRatio2 = theta[5]
            f2 = 10**fluxRatio2
        
        
        if fixedBH:
            alpha_SgrA = -0.5
        else:
            alpha_SgrA = theta[6]

        fluxRatioBG = theta[7]
        if fixedBG:
            alpha_bg = 3.
        else:
            alpha_bg = theta[8]
        
        if self.fit_for[3] == 0:
            pc_RA = 0
            pc_DEC = 0
        else:
            pc_RA = theta[9]
            pc_DEC = theta[10]
            
        try:
            alpha_S = self.source2alpha
            if self.verbose:
                print('Alpha of second star is %.2f' % alpha_S)
        except:
            alpha_S = 3
        
        
        if phasemaps:
            cor_amp_sgr, cor_pha_sgr, cor_int_sgr = self.readPhasemaps(pc_RA,
                                                                       pc_DEC,
                                                                       fromFits=False, 
                                                                       northangle=northangle,
                                                                       dra=dra, ddec=ddec,
                                                                       interp=interppm)
            cor_amp_s2, cor_pha_s2, cor_int_s2 = self.readPhasemaps(dRA+pc_RA, 
                                                                    dDEC+pc_DEC,
                                                                    fromFits=False, 
                                                                    northangle=northangle,
                                                                    dra=dra, ddec=ddec,
                                                                    interp=interppm)
            if fixS29:
                try:
                    cor_amp_s62, cor_pha_s62, cor_int_s62 = self.cor_amp_s62,self. cor_pha_s62, self.cor_int_s62
                except AttributeError: 
                    cor_amp_s62, cor_pha_s62, cor_int_s62 = self.readPhasemaps(dRA2+pc_RA, 
                                                                        dDEC2+pc_DEC,
                                                                        fromFits=False, 
                                                                        northangle=northangle,
                                                                        dra=dra, ddec=ddec,
                                                                        interp=interppm)
                    self.cor_amp_s62,self. cor_pha_s62, self.cor_int_s62 = cor_amp_s62, cor_pha_s62, cor_int_s62
            else:
                cor_amp_s62, cor_pha_s62, cor_int_s62 = self.readPhasemaps(dRA2+pc_RA, 
                                                                        dDEC2+pc_DEC,
                                                                        fromFits=False, 
                                                                        northangle=northangle,
                                                                        dra=dra, ddec=ddec,
                                                                        interp=interppm)
            ## SgrA
            pm_amp_sgr = np.array([[cor_amp_sgr[0], cor_amp_sgr[1]],
                                    [cor_amp_sgr[0], cor_amp_sgr[2]],
                                    [cor_amp_sgr[0], cor_amp_sgr[3]],
                                    [cor_amp_sgr[1], cor_amp_sgr[2]],
                                    [cor_amp_sgr[1], cor_amp_sgr[3]],
                                    [cor_amp_sgr[2], cor_amp_sgr[3]]])
            pm_pha_sgr = np.array([[cor_pha_sgr[0], cor_pha_sgr[1]],
                                    [cor_pha_sgr[0], cor_pha_sgr[2]],
                                    [cor_pha_sgr[0], cor_pha_sgr[3]],
                                    [cor_pha_sgr[1], cor_pha_sgr[2]],
                                    [cor_pha_sgr[1], cor_pha_sgr[3]],
                                    [cor_pha_sgr[2], cor_pha_sgr[3]]])
            pm_int_sgr = np.array([[cor_int_sgr[0], cor_int_sgr[1]],
                                    [cor_int_sgr[0], cor_int_sgr[2]],
                                    [cor_int_sgr[0], cor_int_sgr[3]],
                                    [cor_int_sgr[1], cor_int_sgr[2]],
                                    [cor_int_sgr[1], cor_int_sgr[3]],
                                    [cor_int_sgr[2], cor_int_sgr[3]]])
            ## S2
            pm_amp_s2 = np.array([[cor_amp_s2[0], cor_amp_s2[1]],
                                    [cor_amp_s2[0], cor_amp_s2[2]],
                                    [cor_amp_s2[0], cor_amp_s2[3]],
                                    [cor_amp_s2[1], cor_amp_s2[2]],
                                    [cor_amp_s2[1], cor_amp_s2[3]],
                                    [cor_amp_s2[2], cor_amp_s2[3]]])
            pm_pha_s2 = np.array([[cor_pha_s2[0], cor_pha_s2[1]],
                                    [cor_pha_s2[0], cor_pha_s2[2]],
                                    [cor_pha_s2[0], cor_pha_s2[3]],
                                    [cor_pha_s2[1], cor_pha_s2[2]],
                                    [cor_pha_s2[1], cor_pha_s2[3]],
                                    [cor_pha_s2[2], cor_pha_s2[3]]])   
            pm_int_s2 = np.array([[cor_int_s2[0], cor_int_s2[1]],
                                    [cor_int_s2[0], cor_int_s2[2]],
                                    [cor_int_s2[0], cor_int_s2[3]],
                                    [cor_int_s2[1], cor_int_s2[2]],
                                    [cor_int_s2[1], cor_int_s2[3]],
                                    [cor_int_s2[2], cor_int_s2[3]]])   
            
            ## S62
            pm_amp_s62 = np.array([[cor_amp_s62[0], cor_amp_s62[1]],
                                    [cor_amp_s62[0], cor_amp_s62[2]],
                                    [cor_amp_s62[0], cor_amp_s62[3]],
                                    [cor_amp_s62[1], cor_amp_s62[2]],
                                    [cor_amp_s62[1], cor_amp_s62[3]],
                                    [cor_amp_s62[2], cor_amp_s62[3]]])
            pm_pha_s62 = np.array([[cor_pha_s62[0], cor_pha_s62[1]],
                                    [cor_pha_s62[0], cor_pha_s62[2]],
                                    [cor_pha_s62[0], cor_pha_s62[3]],
                                    [cor_pha_s62[1], cor_pha_s62[2]],
                                    [cor_pha_s62[1], cor_pha_s62[3]],
                                    [cor_pha_s62[2], cor_pha_s62[3]]])   
            pm_int_s62 = np.array([[cor_int_s62[0], cor_int_s62[1]],
                                    [cor_int_s62[0], cor_int_s62[2]],
                                    [cor_int_s62[0], cor_int_s62[3]],
                                    [cor_int_s62[1], cor_int_s62[2]],
                                    [cor_int_s62[1], cor_int_s62[3]],
                                    [cor_int_s62[2], cor_int_s62[3]]])  
            vis = np.zeros((6,len(wave))) + 0j
            for i in range(0,6):
                try:
                    if self.fit_for[3] == 0:
                        phaseCenterRA = 0
                        phaseCenterDEC = 0
                except AttributeError:
                    pass
                s_SgrA = ((pc_RA)*u[i] + (pc_DEC)*v[i]) * mas2rad * 1e6
                s_S1 = ((dRA+pc_RA)*u[i] + (dDEC+pc_DEC)*v[i]) * mas2rad * 1e6
                s_S2 = ((dRA2+pc_RA)*u[i] + (dDEC2+pc_DEC)*v[i]) * mas2rad * 1e6

                
                opd_sgr = (pm_pha_sgr[i,0] - pm_pha_sgr[i,1])/360*wave
                opd_s2 = (pm_pha_s2[i,0] - pm_pha_s2[i,1])/360*wave
                opd_s62 = (pm_pha_s62[i,0] - pm_pha_s62[i,1])/360*wave
                s_SgrA -= opd_sgr
                s_S1 -= opd_s2
                s_S2 -= opd_s62
                
                cr1 = (pm_amp_s2[i,0] / pm_amp_sgr[i,0])**2
                cr2 = (pm_amp_s2[i,1] / pm_amp_sgr[i,1])**2
                cr3 = (pm_amp_s62[i,0] / pm_amp_sgr[i,0])**2 
                cr4 = (pm_amp_s62[i,1] / pm_amp_sgr[i,1])**2 
                
                cr_denom1 = (pm_int_s2[i,0] / pm_int_sgr[i,0])
                cr_denom2 = (pm_int_s2[i,1] / pm_int_sgr[i,1])
                cr_denom3 = (pm_int_s62[i,0] / pm_int_sgr[i,0])
                cr_denom4 = (pm_int_s62[i,1] / pm_int_sgr[i,1])
                
                if approx == "approx":
                    intSgrA = self.vis_intensity_approx(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_approx(0, alpha_SgrA, wave, dlambda[i,:])

                    intS1 = self.vis_intensity_approx(s_S1, alpha_S, wave, dlambda[i,:])
                    intS1_center = self.vis_intensity_approx(0, alpha_S, wave, dlambda[i,:])

                    intS2 = self.vis_intensity_approx(s_S2, alpha_S, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_approx(0, alpha_S, wave, dlambda[i,:])

                    intBG = self.vis_intensity_approx(0, alpha_bg, wave, dlambda[i,:])
                    
                elif approx == "analytic":
                    intSgrA = self.vis_intensity(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity(0, alpha_SgrA, wave, dlambda[i,:])

                    intS1 = self.vis_intensity(s_S1, alpha_S, wave, dlambda[i,:])
                    intS1_center = self.vis_intensity(0, alpha_S, wave, dlambda[i,:])

                    intS2 = self.vis_intensity(s_S2, alpha_S, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity(0, alpha_S, wave, dlambda[i,:])

                    intBG = self.vis_intensity(0, alpha_bg, wave, dlambda[i,:])                
                    
                elif approx == "numeric":
                    intSgrA = self.vis_intensity_num(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_num(0, alpha_SgrA, wave, dlambda[i,:])

                    intS1 = self.vis_intensity_num(s_S1, alpha_S, wave, dlambda[i,:])
                    intS1_center = self.vis_intensity_num(0, alpha_S, wave, dlambda[i,:])

                    intS2 = self.vis_intensity_num(s_S2, alpha_S, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_num(0, alpha_S, wave, dlambda[i,:])

                    intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda[i,:])                
                    intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda[i,:])

                else:
                    raise ValueError('approx has to be approx, analytic or numeric')
                
                vis[i,:] = ((intSgrA + 
                            np.sqrt(f * f * cr1 * cr2) * intS1 +
                            np.sqrt(f2 * f2 * cr3 * cr4) * intS2)/
                            (np.sqrt(intSgrA_center + 
                                     f * cr_denom1 * intS1_center + 
                                     f2 * cr_denom3 * intS2_center +
                                     fluxRatioBG * intBG) *
                             np.sqrt(intSgrA_center + 
                                     f * cr_denom2 * intS1_center +
                                     f2 * cr_denom4 * intS2_center +
                                     fluxRatioBG * intBG)))  
                             
                             
                #vis[i,:] = ((intSgrA + 
                             #f*intS1 + f2*intS2)/
                            #(intSgrA_center + f*intS1_center + f2*intS2_center + fluxRatioBG*intBG))
        else:
            vis = np.zeros((6,len(wave))) + 0j
            for i in range(0,6):
                s_SgrA = ((pc_RA)*u[i] + (pc_DEC)*v[i]) * mas2rad * 1e6
                s_S1 = ((dRA+pc_RA)*u[i] + (dDEC+pc_DEC)*v[i]) * mas2rad * 1e6
                s_S2 = ((dRA2+pc_RA)*u[i] + (dDEC2+pc_DEC)*v[i]) * mas2rad * 1e6
                
                if approx == "approx":
                    intSgrA = self.vis_intensity_approx(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_approx(0, alpha_SgrA, wave, dlambda[i,:])

                    intS1 = self.vis_intensity_approx(s_S1, alpha_S, wave, dlambda[i,:])
                    intS1_center = self.vis_intensity_approx(0, alpha_S, wave, dlambda[i,:])

                    intS2 = self.vis_intensity_approx(s_S2, alpha_S, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_approx(0, alpha_S, wave, dlambda[i,:])

                    intBG = self.vis_intensity_approx(0, alpha_bg, wave, dlambda[i,:])
                    
                elif approx == "analytic":
                    intSgrA = self.vis_intensity(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity(0, alpha_SgrA, wave, dlambda[i,:])

                    intS1 = self.vis_intensity(s_S1, alpha_S, wave, dlambda[i,:])
                    intS1_center = self.vis_intensity(0, alpha_S, wave, dlambda[i,:])

                    intS2 = self.vis_intensity(s_S2, alpha_S, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity(0, alpha_S, wave, dlambda[i,:])

                    intBG = self.vis_intensity(0, alpha_bg, wave, dlambda[i,:])                
                    
                elif approx == "numeric":
                    intSgrA = self.vis_intensity_num(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_num(0, alpha_SgrA, wave, dlambda[i,:])

                    intS1 = self.vis_intensity_num(s_S1, alpha_S, wave, dlambda[i,:])
                    intS1_center = self.vis_intensity_num(0, alpha_S, wave, dlambda[i,:])

                    intS2 = self.vis_intensity_num(s_S2, alpha_S, wave, dlambda[i,:])
                    intS2_center = self.vis_intensity_num(0, alpha_S, wave, dlambda[i,:])

                    intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda[i,:])                
                    intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda[i,:])

                else:
                    raise ValueError('approx has to be approx, analytic or numeric')
                
                vis[i,:] = ((intSgrA + f*intS1 + f2*intS2)/
                            (intSgrA_center + f*intS1_center + f2*intS2_center + fluxRatioBG*intBG))

        visamp = np.abs(vis)
        visphi = np.angle(vis, deg=True)
        closure = np.zeros((4, len(wave)))
        closamp = np.zeros((4, len(wave)))
        for idx in range(4):
            closure[idx] = visphi[self.bispec_ind[idx,0]] + visphi[self.bispec_ind[idx,1]] - visphi[self.bispec_ind[idx,2]]
            closamp[idx] = visamp[self.bispec_ind[idx,0]] * visamp[self.bispec_ind[idx,1]] * visamp[self.bispec_ind[idx,2]]

        visphi = visphi + 360.*(visphi<-180.) - 360.*(visphi>180.)
        closure = closure + 360.*(closure<-180.) - 360.*(closure>180.)
        return visamp, visphi, closure, closamp



    def fitTriple(self, 
                  dRA2, 
                  dDEC2, 
                  nthreads=4, 
                  nwalkers=500, 
                  nruns=500,
                  bestchi=True, 
                  bequiet=False, 
                  fit_for=np.array([0.5,0.5,1.0,0.0,0.0]),
                  approx='approx',
                  donotfit=False, 
                  donotfittheta=None, 
                  onlypol1=False, 
                  initial=None,
                  fixS29=False,
                  dRA=0., 
                  dDEC=0., 
                  dphRA=0.1, 
                  dphDec=0.1,
                  flagtill=3, 
                  flagfrom=13, 
                  fixedBG=True, 
                  noS2=True, 
                  fixpos=False, 
                  fixedBH=False, 
                  plot=True, 
                  plotres=True, 
                  createpdf=True,
                  redchi2=False,
                  phasemaps=False,
                  interppm=True,
                  smoothkernel=15,):
        '''
        Tripple fit to GRAVITY data, reduced version of binary fit
        Parameter:
        dRA2:           Initial guess for position of 3rd source
        dDEC2:          Initial guess for position of 3rd source 
        
        nthreads:       number of cores [4] 
        nwalkers:       number of walkers [500] 
        nruns:          number of MCMC runs [500] 
        bestchi:        Gives best chi2 (for True) or mcmc res as output [True]
        bequiet:        Suppresses ALL outputs
        fit_for:        weight of VA, V2, T3, VP, T3AMP [[0.5,0.5,1.0,0.0,0.0]] 
        approx:         Kind of integration for visibilities (approx, numeric, analytic)
        donotfit:       Only gives fitting results for parameters from donotfittheta [False]
        donotfittheta:  has to be given for donotfit [None]
        onlypol1:       Only fits polarization 1 for split mode [False]
        initial:        Initial guess for fit [None]
        dRA:            Initial guess for dRA (taken from SOFFX if 0) [0]
        dDEC:           Initial guess for dDEC (taken from SOFFY if 0) [0]
        dphRA:          Initial guess for phase center RA [0]
        dphDec:         Initial guess for phase center DEC [0]
        flagtill:       Flag blue channels, has to be changed for not LOW [3] 
        flagfrom:       Flag red channels, has to be changed for not LOW [13]
        fixedBG:        Fir for background power law [False]
        noS2:           Does not do anything if OFFX and OFFY=0
        fixpos:         Does not fit the distance between the sources [False]
        fixedBH:        Fit for black hole power law [False]
        plot:           plot MCMC results [True]
        plotres:        plot fit result [True]
        createpdf:      Creates a pdf with fit results and all plots [True] 
        redchi2:        Gives redchi2 instead of chi2 [False]
        '''
        if self.resolution != 'LOW' and flagtill == 3 and flagfrom == 13:
            raise ValueError('Initial values for flagtill and flagfrom have to be changed if not low resolution')
        self.fit_for = fit_for
        self.fixedBG = fixedBG
        self.fixpos = fixpos
        self.fixedBH = fixedBH
        self.approx = approx
        self.fixS29 = fixS29
        self.donotfit = donotfit
        self.phasemaps = phasemaps
        self.smoothkernel = smoothkernel
        self.interppm = interppm
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
            
        rad2as = 180 / np.pi * 3600
        
        nwave = self.channel
        self.getIntdata(plot=False, flag=False)
        MJD = fits.open(self.name)[0].header["MJD-OBS"]
        u = self.u
        v = self.v
        wave = self.wlSC
            
        self.fiberOffX = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFX"] 
        self.fiberOffY = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFY"] 
        if not bequiet:
            print("fiber center: %.2f, %.2f (mas)" % (self.fiberOffX,
                                                    self.fiberOffY))
        if dRA == 0 and dDEC == 0:
            if self.fiberOffX != 0 and self.fiberOffY != 0:
                dRA = self.fiberOffX
                dDEC = self.fiberOffY
            if self.fiberOffX == 0 and self.fiberOffY == 0:
                if noS2:
                    if not bequiet:
                        print('No Fiber offset, if you want to fit this file use noS2=False')
                    return 0
            if dRA == 0 and dDEC == 0:
                if not bequiet:
                    print('Fiber offset is zero, guess for dRA & dDEC should be given with function')
        else:
            print('Guess for RA & DEC from function as: %.2f, %.2f' % (dRA, dDEC))
            
        self.wave = wave
        self.getDlambda()
        dlambda = self.dlambda
        results = []
        print(len(initial))
        # Initial guesses
        if initial is not None:
            if len(initial) != 11:
                raise ValueError('Length of initial parameter list is not correct')
            size = 2
            dRA_init = np.array([initial[0],initial[0]-size,initial[0]+size])
            dDEC_init = np.array([initial[1],initial[1]-size,initial[1]+size])
            flux_ratio_init = np.array([np.log10(initial[2]), np.log10(0.01), np.log10(100.)])

            dRA2_init = np.array([initial[3],initial[3]-size,initial[3]+size])
            dDEC2_init = np.array([initial[4],initial[4]-size,initial[4]+size])
            flux_ratio2_init = np.array([np.log10(initial[5]), np.log10(0.01), np.log10(100.)])

            alpha_SgrA_init = np.array([initial[6],-5.,7.])
            flux_ratio_bg_init = np.array([initial[7],0.,20.])
            color_bg_init = np.array([initial[8],-5.,7.])

            size = 2
            phase_center_RA_init = np.array([initial[9],initial[9]-size,initial[9]+size])
            phase_center_DEC_init = np.array([initial[10],initial[10]-size,initial[10]+size])

        else:
            size = 4
            dRA_init = np.array([dRA,dRA-size,dRA+size])
            dDEC_init = np.array([dDEC,dDEC-size,dDEC+size])

            dRA2_init = np.array([dRA2,dRA2-size,dRA2+size])
            dDEC2_init = np.array([dDEC2,dDEC2-size,dDEC2+size])

            fr_start = np.log10(0.1)
            flux_ratio_init = np.array([fr_start, np.log10(0.01), np.log10(100.)])
            fr2_start = np.log10(0.1)
            flux_ratio2_init = np.array([fr2_start, np.log10(0.01), np.log10(100.)])

            alpha_SgrA_init = np.array([-1.,-10.,10.])
            flux_ratio_bg_init = np.array([0.1,0.,20.])
            color_bg_init = np.array([3.,-10.,10.])

            size = 5
            phase_center_RA_init = np.array([dphRA,dphRA-size,dphRA+size])
            phase_center_DEC_init = np.array([dphDec,dphDec-size,dphDec+size])
            
        # initial fit parameters 
        theta = np.array([dRA_init[0], dDEC_init[0], flux_ratio_init[0],
                          dRA2_init[0], dDEC2_init[0], flux_ratio2_init[0],
                          alpha_SgrA_init[0], flux_ratio_bg_init[0], color_bg_init[0], 
                          phase_center_RA_init[0], phase_center_DEC_init[0]])

        # lower limit on fit parameters 
        theta_lower = np.array([dRA_init[1], dDEC_init[1], flux_ratio_init[1],
                                dRA2_init[1], dDEC2_init[1], flux_ratio2_init[1],
                                alpha_SgrA_init[1], flux_ratio_bg_init[1], color_bg_init[1], 
                                phase_center_RA_init[1], phase_center_DEC_init[1]])
        
        # upper limit on fit parameters 
        theta_upper = np.array([dRA_init[2], dDEC_init[2], flux_ratio_init[2],
                                dRA2_init[2], dDEC2_init[2], flux_ratio2_init[2],
                                alpha_SgrA_init[2], flux_ratio_bg_init[2], color_bg_init[2], 
                                phase_center_RA_init[2], phase_center_DEC_init[2]])

        theta_names = np.array(["dRA", "dDEC", "fr", "dRA2", "dDEC2", "fr2",
                                r"$\alpha_{flare}$", r"$f_{bg}$", r"$\alpha_{bg}$", 
                                r"$RA_{PC}$", r"$DEC_{PC}$"])
        theta_names_raw = np.array(["dRA", "dDEC", "fr", "dRA2", "dDEC2", "fr2",
                                    "alpha flare", "f BG", "alpha BG", "PC RA", "PC DEC"])
        
        ndim = len(theta)
        todel = []
        if fixpos:
            todel.append(0)
            todel.append(1)
        if fixS29:
            todel.append(3)
            todel.append(4)
            todel.append(5)
        if fixedBH:
            todel.append(6)
        if fixedBG:
            todel.append(8)
        if fit_for[3] == 0:
            todel.append(9)
            todel.append(10)
        ndof = ndim - len(todel)

        if donotfit:
            if donotfittheta is None:
                raise ValueError('If donotfit is True, fit values have to be given by donotfittheta')
            if len(donotfittheta) != ndim:
                print(theta_names_raw)
                raise ValueError('donotfittheta has to have %i parameters, see above' % ndim)
            if plot:
                raise ValueError('If donotfit is True, cannot create MCMC plots')
            if createpdf:
                raise ValueError('If donotfit is True, createpdf should be False')
            print('Will not fit the data, just print out the results for the given theta')
            #donotfittheta[2] = np.log10(donotfittheta[2])
            #donotfittheta[5] = np.log10(donotfittheta[5])
            
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
            polnom = 2
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
            polnom = 1

        for dit in range(ndit):
            if not bequiet and not donotfit:
                print('Run MCMC for DIT %i' % (dit+1))
            ditstart = dit*6
            ditstop = ditstart + 6
            t3ditstart = dit*4
            t3ditstop = t3ditstart + 4
            
            if onlypol1:
                polnom = 1
                
            for idx in range(polnom):
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
            
                with np.errstate(invalid='ignore'):
                    vis2_flag1 = (vis2 > 1) | (vis2 < 1.e-5) 
                vis2_flag2 = np.isnan(vis2)
                vis2_flag_final = ((vis2_flag) | (vis2_flag1) | (vis2_flag2))
                vis2_flag = vis2_flag_final
                vis2 = np.nan_to_num(vis2)
                vis2_error[vis2_flag] = 1.

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
                    if not donotfit:
                        print('Run MCMC for Pol %i' % (idx+1))
                    else:
                        print('Pol %i' % (idx+1))
                fitdata = [visamp, visamp_error, visamp_flag,
                            vis2, vis2_error, vis2_flag,
                            closure, closure_error, closure_flag,
                            visphi, visphi_error, visphi_flag,
                            closamp, closamp_error, closamp_flag]
                
                self.fitstuff = [fitdata, u, v, wave, dlambda, theta]

                if not donotfit:
                    if nthreads == 1:
                        sampler = emcee.EnsembleSampler(nwalkers, ndim, self.lnprob3, 
                                                        args=(fitdata, u, v, wave,
                                                                dlambda, theta_lower,
                                                                theta_upper))
                        if bequiet:
                            sampler.run_mcmc(pos, nruns, progress=False)
                        else:
                            sampler.run_mcmc(pos, nruns, progress=True)
                    else:
                        with Pool(processes=nthreads) as pool:
                            sampler = emcee.EnsembleSampler(nwalkers, ndim, self.lnprob3, 
                                                            args=(fitdata, u, v, wave,
                                                                dlambda, theta_lower,
                                                                theta_upper),
                                                            pool=pool)
                            if bequiet:
                                sampler.run_mcmc(pos, nruns, progress=False) 
                            else:
                                sampler.run_mcmc(pos, nruns, progress=True)     

                    if not bequiet:
                        print("---------------------------------------")
                        print("Mean acceptance fraction: %.2f"  %
                              np.mean(sampler.acceptance_fraction))
                        print("---------------------------------------")

                    samples = sampler.chain
                    mostprop = sampler.flatchain[np.argmax(sampler.flatlnprobability)]

                    clsamples = np.delete(samples, todel, 2)
                    cllabels = np.delete(theta_names, todel)
                    cllabels_raw = np.delete(theta_names_raw, todel)
                    clmostprop = np.delete(mostprop, todel)
                    
                    cldim = len(cllabels)
                    if plot:
                        fig, axes = plt.subplots(cldim, figsize=(8, cldim/1.5),
                                                sharex=True)
                        for i in range(cldim):
                            ax = axes[i]
                            ax.plot(clsamples[:, :, i].T, "k", alpha=0.3)
                            ax.set_ylabel(cllabels[i])
                            ax.yaxis.set_label_coords(-0.1, 0.5)
                        axes[-1].set_xlabel("step number")
                        plt.show()                            

                    if nruns > 299:
                        fl_samples = samples[:, -200:, :].reshape((-1, ndim))
                        fl_clsamples = clsamples[:, -200:, :].reshape((-1, cldim))                
                    elif nruns > 199:
                        fl_samples = samples[:, -100:, :].reshape((-1, ndim))
                        fl_clsamples = clsamples[:, -100:, :].reshape((-1, cldim))   
                    else:
                        fl_samples = samples.reshape((-1, ndim))
                        fl_clsamples = clsamples.reshape((-1, cldim))

                    if plot:
                        ranges = np.percentile(fl_clsamples, [3, 97], axis=0).T
                        fig = corner.corner(fl_clsamples, quantiles=[0.16, 0.5, 0.84],
                                            truths=clmostprop, labels=cllabels)
                        plt.show()

                    # get the actual fit
                    theta_fit = np.percentile(fl_samples, [50], axis=0).T
                    if bestchi:
                        theta_result = mostprop
                    else:
                        theta_result = theta_fit
                else:
                    theta_result = donotfittheta

                results.append(theta_result)
                fit_visamp, fit_visphi, fit_closure, fit_closamp = self.calc_vis3(theta_result, u, v, wave, dlambda)
                fit_vis2 = fit_visamp**2.
                        
                res_visamp = fit_visamp-visamp
                res_vis2 = fit_vis2-vis2
                res_closamp = fit_closamp-closamp
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
                redchi_closamp = np.sum(res_closamp**2./closamp_error**2.*(1-closamp_flag))
                redchi_visphi = np.sum(res_visphi**2./visphi_error**2.*(1-visphi_flag))
                
                
                if redchi2:
                    redchi_visamp /= (visamp.size-np.sum(visamp_flag)-ndof)
                    redchi_vis2 /= (vis2.size-np.sum(vis2_flag)-ndof)
                    redchi_closure /= (closure.size-np.sum(closure_flag)-ndof)
                    redchi_closamp /= (closamp.size-np.sum(closamp_flag)-ndof)
                    redchi_visphi /= (visphi.size-np.sum(visphi_flag)-ndof)
                    chi2string = 'red. chi2'
                else:
                    chi2string = 'chi2'
                
                redchi = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi, redchi_closamp]
                if idx == 0:
                    redchi0 = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi, redchi_closamp]
                elif idx == 1:
                    redchi1 = [redchi_visamp, redchi_vis2, redchi_closure, redchi_visphi, redchi_closamp]
                    
                if not bequiet:
                    print('\n')
                    print('ndof: %i' % (vis2.size-np.sum(vis2_flag)-ndof))
                    print(chi2string + " for visamp: %.2f" % redchi_visamp)
                    print(chi2string + " for vis2: %.2f" % redchi_vis2)
                    print(chi2string + " for visphi: %.2f" % redchi_visphi)
                    print(chi2string + " for closure: %.2f" % redchi_closure)
                    print(chi2string + " for closamp: %.2f" % redchi_closamp)
                    print('\n')

                if not donotfit:
                    percentiles = np.percentile(fl_clsamples, [16, 50, 84],axis=0).T
                    percentiles[:,0] = percentiles[:,1] - percentiles[:,0] 
                    percentiles[:,2] = percentiles[:,2] - percentiles[:,1] 
                    
                    if not bequiet:
                        print("-----------------------------------")
                        print("Best chi2 result:")
                        for i in range(0, cldim):
                            print("%s = %.3f" % (cllabels_raw[i], clmostprop[i]))
                        print("\n")
                        print("MCMC Result:")
                        for i in range(0, cldim):
                            print("%s = %.3f + %.3f - %.3f" % (cllabels_raw[i],
                                                                percentiles[i,1], 
                                                                percentiles[i,2], 
                                                                percentiles[i,0]))
                        print("-----------------------------------")

                if plotres:
                    self.plotFit(theta_result, fitdata, idx, createpdf=False, mode='triple')

        if not bequiet:
            fitted = 1-(np.array(self.fit_for)==0)
            redchi0_f = np.sum(redchi0*fitted)
            if polnom < 2:
                redchi1 = np.zeros_like(redchi0)
            redchi1_f = np.sum(redchi1*fitted)
            redchi_f = redchi0_f + redchi1_f
            print('Combined %s of fitted data: %.3f' % (chi2string, redchi_f))
        if onlypol1 and ndit == 1:
            return theta_result
        else:
            return results


    def plotFit(self, theta, fitdata, idx=0, createpdf=False, mode='binary', phasemaps=False):
        """
        Calculates the theoretical interferometric data for the given parameters in theta
        and plots them together with the data in fitdata.
        Mainly used in fitBinary as result plots.
        """
        rad2as = 180 / np.pi * 3600
        
        (visamp, visamp_error, visamp_flag, 
         vis2, vis2_error, vis2_flag, 
         closure, closure_error, closure_flag, 
         visphi, visphi_error, visphi_flag,
         closamp, closamp_error, closamp_flag) = fitdata
        try:
            self.fiberOffX = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFX"] 
            self.fiberOffY = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFY"]
        except KeyError:
            self.fiberOffX = 0
            self.fiberOffY = 0
        if phasemaps or self.phasemaps:
            self.loadPhasemaps(interp=self.interppm)
            
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
        if mode == 'binary':
            (model_visamp_full, model_visphi_full, 
            model_closure_full, model_closamp_full)  = self.calc_vis(theta, u, v, wave_model, dlambda_model)
        elif mode == 'triple':
            (model_visamp_full, model_visphi_full, 
            model_closure_full, model_closamp_full)  = self.calc_vis3(theta, u, v, wave_model, dlambda_model)            
        else:
            raise ValueError('Plot mode has to be binary or triple')
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
            plt.ylim(-100,100)
            plt.legend()
            if createpdf:
                plt.title('Polarization %i' % (idx + 1))
                pdfname = '%s_pol%i_7.png' % (savetime, idx)
                plt.savefig(pdfname)
                plt.close()
            else:
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
                plt.show()
            
        # T3amp
        if self.fit_for[4]:
            for i in range(0,4):
                max_u_as_model = self.max_spf[i]/(wave_model*1.e-6) / rad2as
                plt.errorbar(self.spFrequAS_T3[i,:], closamp[i,:]*(1-closamp_flag)[i],
                            closamp_error[i,:]*(1-closamp_flag)[i],
                            color=self.colors_closure[i],ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(self.spFrequAS_T3[i,:], closamp[i,:]*(1-closamp_flag)[i],
                            color=self.colors_closure[i], alpha=0.5)
                plt.plot(max_u_as_model, model_closamp_full[i,:], 
                        color='k', zorder=100)
            plt.xlabel('spatial frequency of largest baseline in triangle (1/arcsec)')
            plt.ylabel('closure Amplitude')
            if createpdf:
                plt.title('Polarization %i' % (idx + 1))
                pdfname = '%s_pol%i_9.png' % (savetime, idx)
                plt.savefig(pdfname)
                plt.close()
            else:
                plt.show()

        
        
        
        
        
    ##################################################################################
    ##################################################################################
    ## Unary Fit
    ##################################################################################
    ################################################################################## 
    
    def simulateUnaryphases(self, dRa, dDec, alpha=None, specialfit=False, 
                            specialpar=None, plot=False, compare=True, uvind=False):
        """
        Test function to see how a given phasecenter shift would look like
        """
        self.fixedBG = True
        self.fixedBH = True
        self.noBG = True
        self.use_opds = False
        self.specialfit = specialfit
        self.specialfit_bl = np.array([1,1,0,0,-1,-1])
        self.michistyle = False
        
        rad2as = 180 / np.pi * 3600
        
        nwave = self.channel
        if nwave != 14:
            raise ValueError('Only usable for 14 channels')
        self.getIntdata(plot=False, flag=False)
        u = self.u
        v = self.v
        wave = self.wlSC_P1
        self.getDlambda()
        dlambda = self.dlambda
        
        if alpha is not None:
            theta = [dRa, dDec, alpha, 0, 0, 0, 0, 0, 0, specialpar]
            self.fixedBH = False
        else:
            theta = [dRa, dDec, 0, 0, 0, 0, 0, 0, 0, specialpar]
            self.fixedBH = True
        fit_visphi = self.calc_vis_unary(theta, u, v, wave, dlambda)
        magu_as = self.spFrequAS
        
        if compare:
            visphi = self.visphiSC_P1[:6]
            visphi_error = self.visphierrSC_P1[:6]
            visphi_flag = self.visampflagSC_P1[:6]
            visphi_flag[:,0:2] = True
            visphi_flag[:,12:] = True
            fitdata = [visphi, visphi_error, visphi_flag]
            self.plotFitUnary(theta, fitdata, u, v, 1,
                              createpdf=False, uvind=uvind)
            
            
            
            res_visphi_1 = fit_visphi-visphi
            #res_visphi_2 = 360-(fit_visphi-visphi)
            #check = np.abs(res_visphi_1) < np.abs(res_visphi_2) 
            #res_visphi = res_visphi_1*check + res_visphi_2*(1-check)
            chi2 = np.sum(res_visphi_1**2./visphi_error**2.*(1-visphi_flag))
            print(chi2)
            return visphi
            
        else:
            if plot:
                wave_model = np.linspace(wave[0],wave[len(wave)-1],1000)
                dlambda_model = np.zeros((6,len(wave_model)))
                for i in range(0,6):
                    dlambda_model[i,:] = np.interp(wave_model, wave, dlambda[i,:])
                model_visphi_full  = self.calc_vis_unary(theta, u, v, wave_model, dlambda_model)
                
                u_as_model = np.zeros((len(u),len(wave_model)))
                v_as_model = np.zeros((len(v),len(wave_model)))
                for i in range(0,len(u)):
                    u_as_model[i,:] = u[i]/(wave_model*1.e-6) / rad2as
                    v_as_model[i,:] = v[i]/(wave_model*1.e-6) / rad2as
                magu_as_model = np.sqrt(u_as_model**2.+v_as_model**2.)
                for i in range(0,6):
                    plt.errorbar(magu_as[i,:], visphi[i,:], color=self.colors_baseline[i], ls='', marker='o')
                    plt.plot(magu_as_model[i,:], model_visphi_full[i,:], color=self.colors_baseline[i],alpha=1.0)
                plt.ylabel('VisPhi')
                plt.xlabel('spatial frequency (1/arcsec)')
                plt.axhline(0, ls='--', lw=0.5)
                maxval = np.max(np.abs(model_visphi_full))
                if maxval < 45:
                    maxplot=50
                elif maxval < 95:
                    maxplot=100
                else:
                    maxplot=180
                plt.ylim(-maxplot, maxplot)
                plt.suptitle('dRa=%.1f, dDec=%.1f' % (theta[0], theta[1]), fontsize=12)
                plt.show()
        return magu_as, fit_visphi
        
    
    def calc_vis_unary(self, theta, u, v, wave, dlambda):
        mas2rad = 1e-3 / 3600 / 180 * np.pi
        rad2mas = 180 / np.pi * 3600 * 1e3             
        fixedBG = self.fixedBG
        fixedBH = self.fixedBH
        noBG = self.noBG
        use_opds = self.use_opds
        specialfit = self.specialfit
        michistyle = self.michistyle
        approx = self.approx
        
        phaseCenterRA = theta[0]
        phaseCenterDEC = theta[1]
        if fixedBH:
            alpha_SgrA = -0.5
        else:
            alpha_SgrA = theta[2]
        if noBG:
            fluxRatioBG = 0
        else:
            fluxRatioBG = theta[3]
        if fixedBG:
            alpha_bg = 3.
        else:
            alpha_bg = theta[4]
        if use_opds:
            opd1 = theta[5]
            opd2 = theta[6]
            opd3 = theta[7]
            opd4 = theta[8]
            opd_bl = np.array([[opd4, opd3],
                               [opd4, opd2],
                               [opd4, opd1],
                               [opd3, opd2],
                               [opd3, opd1],
                               [opd2, opd1]])
        if specialfit:
            special_par = theta[9] 
            sp_bl = np.ones(6)*special_par
            sp_bl *= self.specialfit_bl

        vis = np.zeros((6,len(wave))) + 0j
        if len(u) != 6 or len(v) != 6:
            raise ValueError('u or v have wrong length, something went wrong')                
                
        for i in range(0,6):
            # pc in mas -> mas2rad -> pc in rad
            # uv in m -> *1e6 -> uv in mum
            # s in mum
            s_SgrA = ((phaseCenterRA)*u[i] + (phaseCenterDEC)*v[i]) * mas2rad * 1e6
            
            if use_opds:
                s_SgrA = s_SgrA + opd_bl[i,0] - opd_bl[i,1]
            if specialfit:
                s_SgrA = s_SgrA + sp_bl[i]

            if michistyle:
                s_sgra_ul = s_SgrA / wave
                vis[i,:] = np.exp(-2j*np.pi*s_sgra_ul)
            
            else:
                # interferometric intensities of all components
                if approx == "approx":
                    intSgrA = self.vis_intensity_approx(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_approx(0, alpha_SgrA, wave, dlambda[i,:])
                    intBG = self.vis_intensity_approx(0, alpha_bg, wave, dlambda[i,:])
                elif approx == "analytic":
                    intSgrA = self.vis_intensity(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity(0, alpha_SgrA, wave, dlambda[i,:])
                    intBG = self.vis_intensity(0, alpha_bg, wave, dlambda[i,:])
                elif approx == "numeric":
                    intSgrA = self.vis_intensity_num(s_SgrA, alpha_SgrA, wave, dlambda[i,:])
                    intSgrA_center = self.vis_intensity_num(0, alpha_SgrA, wave, dlambda[i,:])
                    intBG = self.vis_intensity_num(0, alpha_bg, wave, dlambda[i,:])
                else:
                    raise ValueError('approx has to be approx, analytic or numeric')                
                
                vis[i,:] = (intSgrA/
                            (intSgrA_center + fluxRatioBG * intBG))
            

            
        visphi = np.angle(vis, deg=True)
        visphi = visphi + 360.*(visphi<-180.) - 360.*(visphi>180.)
        return visphi   


    def lnlike_unary(self, theta, fitdata, u, v, wave, dlambda):
        model_visphi = self.calc_vis_unary(theta,u,v,wave,dlambda)
        visphi, visphi_error, visphi_flag = fitdata
        res_phi = (-np.minimum((model_visphi-visphi)**2.,
                                     (360-(model_visphi-visphi))**2.)/
                          visphi_error**2.*(1-visphi_flag))
        res_phi = np.sum(res_phi[~np.isnan(res_phi)])
        return 0.5*res_phi
    
    
    def lnprob_unary(self, theta, fitdata, u, v, wave, dlambda, lower, upper):
        if np.any(theta < lower) or np.any(theta > upper):
            return -np.inf
        return self.lnlike_unary(theta, fitdata, u, v, wave, dlambda)
    
    
    def plot_unary(self, theta, giveuv=False, uu=None, vv=None, plot=False):
        """
        Test function to see how a given theta would look like in phases.
        Input:  theta = [dRa, dDec]
                giveuv has to be set to True if you wnat to change the uv data
                otherwie it will take them from the class
        """
        theta_names_raw = np.array(["PC RA", "PC DEC"])
        rad2as = 180 / np.pi * 3600
        try:
            if len(theta) != 2:
                print('Theta has to include the following 2 parameter:')
                print(theta_names_raw)
                raise ValueError('Wrong number of input parameter given (should be 2)')
        except(TypeError):
            print('Thetha has to include the following 2 parameter:')
            print(theta_names_raw)
            raise ValueError('Wrong number of input parameter given (should be 2)')
        self.fixedBG = True
        self.fixedBH = True
        self.noBG = True
        self.use_opds = False
        self.specialfit = False
        nwave = self.channel
        if giveuv:
            if uu is None or vv is None:
                raise ValueError("if giveuv=True values for u and v have to be given to the function (as uu, vv)")
            else:
                u = uu
                v = vv
        else:
            u = self.u
            v = self.v
        wave = self.wlSC_P1
        
        self.getDlambda()
        dlambda = self.dlambda

        visphi = self.calc_vis_unary(theta, u, v, wave, dlambda)
        return visphi
        

    def fitUnary(self, 
                 nthreads=4, 
                 nwalkers=500, 
                 nruns=500, 
                 bestchi=True,
                 bequiet=False, 
                 michistyle=False,
                 approx='approx', 
                 onlypol1=False, 
                 mindatapoints=3,
                 flagtill=2,
                 flagfrom=12,
                 initpos=None,
                 dontfit=None,
                 dontfitbl=None,
                 noS2=False,
                 fixedBG=True,
                 fixedBH=True,
                 noBG=True,
                 fitopds=np.array([0,0,0,0]), 
                 specialpar=np.array([0,0,0,0,0,0]), 
                 plot=True, 
                 plotres=True, 
                 writeresults=True,
                 createpdf=True, 
                 writefitdiff=False,
                 returnPos=False,
                 phaseinput=None):
        """
        Does a MCMC unary fit on the phases of the data.
        Parameter:
        nthreads:       number of cores [4] 
        nwalkers:       number of walkers [500] 
        nruns:          number of MCMC runs [500] 
        bestchi:        Gives best chi2 (for True) or mcmc res as output [True]
        bequiet:        Suppresses ALL outputs [False]
        michistyle:     Uses a simple exponential fit instead of full 
                        visibility calculation [False]
        approx:         Kind of integration for visibilities (approx, numeric, analytic)
        mindatapoints:  if less valid datapoints in one baseline, file is rejected [3]
        onlypol1:       Only fits polarization 1 for split mode [False]
        flagtill:       Flag blue channels [2] 
        flagfrom:       Flag red channels [12]
        dontfit:        Number of telescope to flag
        dontfitbl:      Number of baseline to flag
        noS2:           If True ignores files where fiber offset is 0 [False]
        fixedBG:        Fit for background power law [False]
        fixedBH:        Fit for black hole power law [False]
        noBG:           Sets background flux ratio to 0 [True]
        fitopds:        Fit individual opds id equal 1 [0,0,0,0]
        specialpar:     Allows OPD for individual baseline [0,0,0,0,0,0]
        
        plot:           plot MCMC results [True]
        plotres:        plot fit result [True]
        writeresults:   Write fit results in file [True] 
        createpdf:      Creates a pdf with fit results and all plots [True] 
        writefitdiff:   Writes the difference of the mean fit vs data instead of redchi2
        returnPos:      Retuns fitted position
        """
        if self.resolution != 'LOW' and flagtill == 2 and flagfrom == 12:
            raise ValueError('Initial values for flagtill and flagfrom have to be changed if not low resolution')
        rad2as = 180 / np.pi * 3600
        self.fixedBG = fixedBG
        self.fixedBH = fixedBH
        self.noBG = noBG
        self.michistyle = michistyle
        self.approx = approx
        if np.any(fitopds):
            self.use_opds = True
            use_opds = True
        else:
            self.use_opds = False
        
        if np.any(specialpar):
            self.specialfit = True
            self.specialfit_bl = specialpar
            if not bequiet:
                print('Specialfit parameter applied to BLs:')
                nonzero = np.nonzero(self.specialfit_bl)[0]
                print(*list(nonzero*self.specialfit_bl[nonzero]))
                print('\n')
        else:
            self.specialfit = False
        specialfit = self.specialfit
        
        # Get data from file
        nwave = self.channel
        self.getIntdata(plot=False, flag=False)
        MJD = fits.open(self.name)[0].header["MJD-OBS"]
        fullu = self.u
        fullv = self.v
        wave = self.wlSC
        
        self.fiberOffX = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFX"] 
        self.fiberOffY = -fits.open(self.name)[0].header["HIERARCH ESO INS SOBJ OFFY"] 
        if not bequiet:
            print("fiber center: %.2f, %.2f (mas)" % (self.fiberOffX,
                                                    self.fiberOffY))
        if self.fiberOffX == 0 and self.fiberOffY == 0:
            if noS2:
                if not bequiet:
                    print('No Fiber offset, if you want to fit this file use noS2=False')
                return 0
            
        stname = self.name.find('GRAVI')     
        if dontfit is not None:
            savefilename = 'punaryfit_woUT' + str(dontfit) + '_' + self.name[stname:-5]
        elif dontfitbl is not None:
            if isinstance(dontfitbl, list):
                blname = "".join(str(i) for i in dontfitbl)
                savefilename = 'punaryfit_woBL' + str(blname) + '_' + self.name[stname:-5]
            elif isinstance(dontfitbl, int):
                savefilename = 'punaryfit_woBL' + str(dontfitbl) + '_' + self.name[stname:-5]
        else:
            savefilename = 'punaryfit_' + self.name[stname:-5]

        txtfilename = savefilename + '.txt'
        if writeresults:
            txtfile = open(txtfilename, 'w')
            txtfile.write('# Results of Unary fit for %s \n' % self.name[stname:])
            txtfile.write('# Lines are: Best chi2, MCMC result, MCMC error -, MCMC error + \n')
            txtfile.write('# Rowes are: PC RA, PC DEC, alpha SgrA*, f BG, alpha BG \n')
            txtfile.write('# Parameter which are not fitted have 0.0 as error \n')
            txtfile.write('# MJD: %f \n' % MJD)
            txtfile.write('# OFFX: %f \n' % self.fiberOffX)
            txtfile.write('# OFFY: %f \n\n' % self.fiberOffY)

        self.wave = wave
        self.getDlambda()
        dlambda = self.dlambda

        # Initial guesses
        size = 5
        phase_center_RA = 0.01
        phase_center_DEC = 0.01

        if initpos is not None:
            if len(initpos) != 2:
                raise ValueError('Initpos has to be a list of 2 parameters')
            phase_center_RA = initpos[0]
            phase_center_DEC = initpos[1]

        phase_center_RA_init = np.array([phase_center_RA,
                                         phase_center_RA - size,
                                         phase_center_RA + size])
        phase_center_DEC_init = np.array([phase_center_DEC,
                                          phase_center_DEC - size,
                                          phase_center_DEC + size])
        alpha_SgrA_init = np.array([-1.,-5.,7.])
        flux_ratio_bg_init = np.array([0.1,0.,20.])
        alpha_bg_init = np.array([3.,-5.,5.])
        opd_max = 1.5 # maximum opd in microns
        opd_1_init = [0.0,-opd_max,opd_max]
        opd_2_init = [0.0,-opd_max,opd_max]
        opd_3_init = [0.0,-opd_max,opd_max]
        opd_4_init = [0.0,-opd_max,opd_max]
        special_par = [-0.15, -2, 2]

        # initial fit parameters 
        theta = np.array([phase_center_RA_init[0], phase_center_DEC_init[0],
                          alpha_SgrA_init[0], flux_ratio_bg_init[0], alpha_bg_init[0],
                          opd_1_init[0],opd_2_init[0],opd_3_init[0],opd_4_init[0],
                          special_par[0]])
        theta_lower = np.array([phase_center_RA_init[1], phase_center_DEC_init[1],
                                alpha_SgrA_init[1], flux_ratio_bg_init[1],
                                alpha_bg_init[1],opd_1_init[1],opd_2_init[1],
                                opd_3_init[1],opd_4_init[1], special_par[1]])
        theta_upper = np.array([phase_center_RA_init[2], phase_center_DEC_init[2],
                                alpha_SgrA_init[2], flux_ratio_bg_init[2],
                                alpha_bg_init[2],opd_1_init[2],opd_2_init[2],
                                opd_3_init[2],opd_4_init[2], special_par[2]])

        theta_names = np.array([r"$RA_{PC}$", r"$DEC_{PC}$", r"$\alpha_{SgrA}$", 
                                r"$f_{bg}$",r"$\alpha_{bg}$", "OPD1", "OPD2", 
                                "OPD3", "OPD4", "special"])
        theta_names_raw = np.array(["PC RA", "PC DEC", "alpha SgrA", "f BG", "alpha BG", 
                                    "OPD1", "OPD2", "OPD3", "OPD4", "special"])

        ndim = len(theta)
        todel = []
        if fixedBH:
            todel.append(2)
        if noBG:
            todel.append(3)
        if fixedBG:
            todel.append(4)
        for tel in range(4):
            if fitopds[tel] != 1:
                todel.append(5+tel)
        if not specialfit:
            todel.append(9)
        ndof = ndim - len(todel)
        
        if returnPos:
            returnList = []

        # Get data
        if self.polmode == 'SPLIT':
            if phaseinput is None:
                visphi_P = [self.visphiSC_P1, self.visphiSC_P2]
            else:
                visphi_P = phaseinput
            visphi_error_P = [self.visphierrSC_P1, self.visphierrSC_P2]
            visphi_flag_P = [self.visampflagSC_P1, self.visampflagSC_P2]
            
            ndit = np.shape(self.visampSC_P1)[0]//6
            if not bequiet:
                print('NDIT = %i' % ndit)
            polnom = 2
        elif self.polmode == 'COMBINED':
            visphi_P = [self.visphiSC]
            visphi_error_P = [self.visphierrSC]
            visphi_flag_P = [self.visampflagSC]
            
            ndit = np.shape(self.visampSC)[0]//6
            if not bequiet:
                print('NDIT = %i' % ndit)
            polnom = 1
            
        for dit in range(ndit):
            savetime = str(datetime.now()).replace('-', '')
            savetime = savetime.replace(' ', '-')
            savetime = savetime.replace(':', '')
            self.savetime = savetime
            if writeresults and ndit > 1:
                txtfile.write('# DIT %i \n' % dit)
            if createpdf:
                savetime = str(datetime.now()).replace('-', '')
                savetime = savetime.replace(' ', '-')
                savetime = savetime.replace(':', '')
                self.savetime = savetime
                if ndit == 1:
                    pdffilename = savefilename + '.pdf'
                else:
                    pdffilename = savefilename + '_DIT' + str(dit) + '.pdf'

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

                pdf.cell(40, 6, txt="Fixed Bg", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fixedBG), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Fixed BH", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(fixedBH), ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Flag before/after", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(flagtill) + '/' + str(flagfrom), 
                        ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Result: Best Chi2", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(bestchi), ln=1, align="L", border=0)
                
                pdf.cell(40, 6, txt="Specialfit", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(specialfit), ln=0, align="L", border=0)
                pdf.cell(40, 6, txt="Specialfit BL", ln=0, align="L", border=0)
                pdf.cell(40, 6, txt=str(specialpar), ln=1, align="L", border=0)
                pdf.ln()

            if not bequiet:
                print('Run MCMC for DIT %i' % (dit+1))
            ditstart = dit*6
            ditstop = ditstart + 6


            if onlypol1:
                polnom = 1
            bothdofit = np.ones(polnom)            
            for idx in range(polnom):
                visphi = visphi_P[idx][ditstart:ditstop]
                visphi_error = visphi_error_P[idx][ditstart:ditstop]
                visphi_flag = visphi_flag_P[idx][ditstart:ditstop]
                u = fullu[ditstart:ditstop]
                v = fullv[ditstart:ditstop]
                
                if ((flagtill > 0) and (flagfrom > 0)):
                    p = flagtill
                    t = flagfrom
                    if idx == 0 and dit == 0:
                        if not bequiet:
                            print('using channels from #%i to #%i' % (p, t))
                    visphi_flag[:,0:p] = True
                    visphi_flag[:,t:] = True
                    
                # check if the data is good enough to fit
                dofit = True
                if (u == 0.).any():
                    if not bequiet:
                        print('some values in u are zero, something wrong in the data')
                    dofit = False
                if (v == 0.).any():
                    if not bequiet:
                        print('some values in v are zero, something wrong in the data')
                    dofit = False
                for bl in range(6):
                    if (visphi_flag[bl] == True).all():
                        if not bequiet:
                            print('Baseline %i is completely flagged, something wrong with the data' % bl)
                        dofit = False
                    elif (np.size(visphi_flag[bl])-np.count_nonzero(visphi_flag[bl])) < mindatapoints:
                        if not bequiet:
                            print('Baseline %i is has to few non flagged values' % bl)
                        dofit = False
                bothdofit[idx] = dofit
                
                if dontfit is not None:
                    if not bequiet:
                        print('Will not fit Telescope %i' % dontfit)
                    if dontfit not in [1,2,3,4]:
                        raise ValueError('Dontfit has to be one of the UTs: 1,2,3,4')
                    telescopes = [[4, 3],[4, 2],[4, 1],[3, 2],[3, 1],[2, 1]]
                    for bl in range(6):
                        if dontfit in telescopes[bl]:
                            visphi_flag[bl,:] = True
                if dontfitbl is not None:
                    if isinstance(dontfitbl, int):
                        if not bequiet:
                            print('Will not fit baseline %i' % dontfitbl)
                        if dontfitbl not in [1,2,3,4,5,6]:
                            raise ValueError('Dontfit has to be one of the UTs: 1,2,3,4,5,6')
                        if dontfit is not None:
                            raise ValueError('Use either dontfit or dontfitbl, not both')
                        visphi_flag[dontfitbl-1,:] = True
                    elif isinstance(dontfitbl, list):
                        for bl in dontfitbl:
                            if not bequiet:
                                print('Will not fit baseline %i' % bl)
                            if bl not in [1,2,3,4,5,6]:
                                raise ValueError('Dontfit has to be one of the UTs: 1,2,3,4,5,6')
                            if dontfit is not None:
                                raise ValueError('Use either dontfit or dontfitbl, not both')
                            visphi_flag[bl-1,:] = True
                            
                        
                    
                        
                        
                if dofit == True:
                    width = 1e-1
                    pos = np.ones((nwalkers,ndim))
                    for par in range(ndim):
                        if par in todel:
                            pos[:,par] = theta[par]
                        else:
                            pos[:,par] = theta[par] + width*np.random.randn(nwalkers)
                    if not bequiet:
                        print('Run MCMC for Pol %i' % (idx+1))
                    fitdata = [visphi, visphi_error, visphi_flag]
                    if nthreads == 1:
                        sampler = emcee.EnsembleSampler(nwalkers, ndim, 
                                                        self.lnprob_unary,
                                                        args=(fitdata, u, v, wave,
                                                            dlambda, theta_lower,
                                                            theta_upper))
                        if bequiet:
                            sampler.run_mcmc(pos, nruns, progress=False)
                        else:
                            sampler.run_mcmc(pos, nruns, progress=True)
                    else:
                        with Pool(processes=nthreads) as pool:
                            sampler = emcee.EnsembleSampler(nwalkers, ndim,
                                                            self.lnprob_unary, 
                                                            args=(fitdata, u, v, wave,
                                                                dlambda, theta_lower,
                                                                theta_upper),
                                                            pool=pool)
                            if bequiet:
                                sampler.run_mcmc(pos, nruns, progress=False) 
                            else:
                                sampler.run_mcmc(pos, nruns, progress=True)     
                            
                    if not bequiet:
                        print("---------------------------------------")
                        print("Mean acceptance fraction: %.2f"  % np.mean(sampler.acceptance_fraction))
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
                    cllabels_raw = np.delete(theta_names_raw, todel)
                    clmostprop = np.delete(mostprop, todel)
                        
                    cldim = len(cllabels)
                    if plot:
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

                    if plot:
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
                        
                    fit_visphi = self.calc_vis_unary(theta_result, u, v, wave, dlambda)
                                
                    res_visphi_1 = fit_visphi-visphi
                    res_visphi_2 = 360-(fit_visphi-visphi)
                    check = np.abs(res_visphi_1) < np.abs(res_visphi_2) 
                    res_visphi = res_visphi_1*check + res_visphi_2*(1-check)

                    redchi_visphi = np.sum((res_visphi**2./visphi_error**2.)*(1-visphi_flag))/(visphi.size-np.sum(visphi_flag)-ndof)
                    #redchi_visphi = np.sum(res_visphi**2.*(1-visphi_flag))/(visphi.size-np.sum(visphi_flag)-ndof)
                        
                    fitdiff = []
                    for bl in range(6):
                        data = visphi[bl]
                        data[np.where(visphi_flag[bl] == True)] = np.nan
                        fit = fit_visphi[bl]
                        fit[np.where(visphi_flag[bl] == True)] = np.nan
                        err = visphi_error[bl]
                        err[np.where(visphi_flag[bl] == True)] = np.nan              
                        fitdiff.append(np.abs(np.nanmedian(data)-np.nanmedian(fit)))
                        #fitdiff.append(np.abs(np.nanmedian(data)-np.nanmedian(fit))/np.nanmean(err))
                    fitdiff = np.sum(fitdiff)
                        
                    if idx == 0:
                        redchi0 = redchi_visphi
                    elif idx == 1:
                        redchi1 = redchi_visphi
                            
                    if not bequiet:
                        print('ndof: %i' % (visphi.size-np.sum(visphi_flag)-ndof))
                        print("redchi for visphi: %.2f" % redchi_visphi)
                        print("mean fit difference: %.2f" % fitdiff)
                        print("average visphi error (deg): %.2f" % 
                                np.mean(visphi_error*(1-visphi_flag)))
                        
                    percentiles = np.percentile(fl_clsamples, [16, 50, 84],axis=0).T
                    percentiles[:,0] = percentiles[:,1] - percentiles[:,0] 
                    percentiles[:,2] = percentiles[:,2] - percentiles[:,1] 
                        
                    if not bequiet:
                        print("-----------------------------------")
                        print("Best chi2 result:")
                        for i in range(0, cldim):
                            print("%s = %.3f" % (cllabels_raw[i], clmostprop[i]))
                        print("\n")
                        print("MCMC Result:")
                        for i in range(0, cldim):
                            print("%s = %.3f + %.3f - %.3f" % (cllabels_raw[i],
                                                                percentiles[i,1], 
                                                                percentiles[i,2], 
                                                                percentiles[i,0]))
                        print("-----------------------------------")
                        
                    if returnPos:
                        returnList.append(percentiles[:,1])
                    
                    if createpdf:
                        pdf.cell(40, 8, txt="", ln=0, align="L", border="B")
                        pdf.cell(40, 8, txt="Best chi2 result", ln=0, align="L", border="LB")
                        pdf.cell(60, 8, txt="MCMC result", ln=1, align="L", border="LB")
                        for i in range(0, cldim):
                            pdf.cell(40, 6, txt="%s" % cllabels_raw[i], 
                                    ln=0, align="L", border=0)
                            pdf.cell(40, 6, txt="%.3f" % clmostprop[i], 
                                    ln=0, align="C", border="L")
                            pdf.cell(60, 6, txt="%.3f + %.3f - %.3f" % 
                                    (percentiles[i,1], percentiles[i,2], percentiles[i,0]),
                                    ln=1, align="C", border="L")
                        pdf.ln()
                        
                    if plotres:
                        self.plotFitUnary(theta_result, fitdata, u, v, idx, 
                                        createpdf=createpdf)
                else:
                    fitdiff = 0
                    redchi_visphi = 0
                if writeresults:
                    if writefitdiff:
                        fitqual = fitdiff
                    else:
                        fitqual = redchi_visphi
                    txtfile.write("# Polarization %i  \n" % (idx+1))
                    if dofit == True:
                        for tdx, t in enumerate(mostprop):
                            txtfile.write(str(t))
                            txtfile.write(', ')
                        txtfile.write(str(fitqual))
                        txtfile.write('\n')
                                
                        percentiles = np.percentile(fl_samples, [16, 50, 84],axis=0).T
                        percentiles[:,0] = percentiles[:,1] - percentiles[:,0] 
                        percentiles[:,2] = percentiles[:,2] - percentiles[:,1] 
                        
                        for tdx, t in enumerate(percentiles[:,1]):
                            txtfile.write(str(t))
                            txtfile.write(', ')
                        txtfile.write(str(fitqual))
                        txtfile.write('\n')

                        for tdx, t in enumerate(percentiles[:,0]):
                            if tdx in todel:
                                txtfile.write(str(t*0.0))
                            else:
                                txtfile.write(str(t))
                            if tdx != (len(percentiles[:,1])-1):
                                txtfile.write(', ')
                            else:
                                txtfile.write(', 0 \n')

                        for tdx, t in enumerate(percentiles[:,2]):
                            if tdx in todel:
                                txtfile.write(str(t*0.0))
                            else:
                                txtfile.write(str(t))
                            if tdx != (len(percentiles[:,1])-1):
                                txtfile.write(', ')
                            else:
                                txtfile.write(', 0 \n')
                    else:
                        nantxt = 'nan, '
                        txtfile.write(nantxt*(ndim) + 'nan \n')
                        txtfile.write(nantxt*(ndim) + 'nan \n')
                        txtfile.write(nantxt*(ndim) + 'nan \n')
                        txtfile.write(nantxt*(ndim) + 'nan \n')
                
            if createpdf:
                if (bothdofit == True).all():
                    pdfimages0 = sorted(glob.glob(savetime + '_pol0*.png'))
                    pdfimages1 = sorted(glob.glob(savetime + '_pol1*.png'))
                    pdfcout = 0
                    if plot:
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
                        
                        if polnom == 2:
                            pdf.add_page()
                            pdf.cell(0, 10, txt="Polarization  2", ln=1, align="C", border='B')
                            pdf.ln()
                            pdf.image(pdfimages1[0], h=he, w=wi)
                            pdf.image(pdfimages1[1], h=115)
                        pdfcout = 2

                    if plotres:
                        titles = ['Visibility Phase']
                        for pa in range(1):
                            pdf.add_page()
                            if polnom == 2:
                                text = '%s, redchi: %.2f (P1), %.2f (P2)' % (titles[pa], 
                                                                            redchi0, 
                                                                            redchi1)
                            else:
                                text = '%s, redchi: %.2f (P1)' % (titles[pa], redchi0)
                            pdf.cell(0, 10, txt=text, ln=1, align="C", border='B')
                            pdf.ln()
                            pdf.image(pdfimages0[pdfcout+pa], w=150)
                            if polnom == 2:
                                pdf.image(pdfimages1[pdfcout+pa], w=150)
                    
                    if not bequiet:
                        print('Save pdf as %s' % pdffilename)
                    pdf.output(pdffilename)
                else:
                    del pdf 
                files = glob.glob(savetime + '_pol?_?.png')
                for file in files:
                    os.remove(file)
        if writeresults:
            txtfile.close()
        if returnPos:
            return np.array(returnList)
        else:
            return 0
    
    
    
    
            
    def plotFitUnary(self, theta,  fitdata, u, v, idx=0, createpdf=False, uvind=False):
        rad2as = 180 / np.pi * 3600
        (visphi, visphi_error, visphi_flag) = fitdata
        visphi[np.isnan(visphi)] = 0
        wave = self.wlSC
        dlambda = self.dlambda
        if createpdf:
            savetime = self.savetime
        wave_model = np.linspace(wave[0],wave[len(wave)-1],1000)
        dlambda_model = np.zeros((6,len(wave_model)))
        for i in range(0,6):
            dlambda_model[i,:] = np.interp(wave_model, wave, dlambda[i,:])
            
        # Fit
        model_visphi_full = self.calc_vis_unary(theta, u, v, wave_model, dlambda_model)
        magu_as = self.spFrequAS
        
        u_as_model = np.zeros((len(u),len(wave_model)))
        v_as_model = np.zeros((len(v),len(wave_model)))
        for i in range(0,len(u)):
            u_as_model[i,:] = u[i]/(wave_model*1.e-6) / rad2as
            v_as_model[i,:] = v[i]/(wave_model*1.e-6) / rad2as
        magu_as_model = np.sqrt(u_as_model**2.+v_as_model**2.)
        
        if uvind:
            gs = gridspec.GridSpec(1,2)
            axis = plt.subplot(gs[0,0])
            for i in range(0,6):
                plt.errorbar(u_as[i,:], visphi[i,:]*(1-visphi_flag[i]), 
                            visphi_error[i,:]*(1-visphi_flag[i]), label=self.baseline_labels[i],
                            color=self.colors_baseline[i], ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(u_as[i,:], visphi[i,:]*(1-visphi_flag[i]),
                            color=self.colors_baseline[i], alpha=0.5)
                plt.plot(u_as_model[i,:], model_visphi_full[i,:],
                        color='k', zorder=100)
            plt.ylabel('visibility phase')
            plt.xlabel('U (1/arcsec)')     

            axis = plt.subplot(gs[0,1])
            for i in range(0,6):
                plt.errorbar(v_as[i,:], visphi[i,:]*(1-visphi_flag[i]), 
                            visphi_error[i,:]*(1-visphi_flag[i]), label=self.baseline_labels[i],
                            color=self.colors_baseline[i], ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(v_as[i,:], visphi[i,:]*(1-visphi_flag[i]),
                            color=self.colors_baseline[i], alpha=0.5)
                plt.plot(v_as_model[i,:], model_visphi_full[i,:],
                        color='k', zorder=100)
            plt.xlabel('V (1/arcsec)')     
            plt.show()
        
        else:
            for i in range(0,6):
                plt.errorbar(magu_as[i,:], visphi[i,:]*(1-visphi_flag[i]), 
                            visphi_error[i,:]*(1-visphi_flag[i]), label=self.baseline_labels[i],
                            color=self.colors_baseline[i], ls='', lw=1, alpha=0.5, capsize=0)
                plt.scatter(magu_as[i,:], visphi[i,:]*(1-visphi_flag[i]),
                            color=self.colors_baseline[i], alpha=0.5)
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
                plt.show()
            