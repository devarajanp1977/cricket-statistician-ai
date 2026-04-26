import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.cricketstats.app',
  appName: 'Cricket Statistician',
  webDir: 'dist',
  backgroundColor: '#FFFFFF',
  android: {
    // Temporary until the public API is served over TLS.
    allowMixedContent: true,
  },
  server: {
    // Temporary until the public API is moved behind HTTPS.
    androidScheme: 'http',
    cleartext: true,
    // For local dev over LAN, uncomment and set:
    // url: 'http://192.168.x.x:5173',
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 800,
      launchAutoHide: true,
      backgroundColor: '#FFFFFF',
      androidSplashResourceName: 'splash',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
    },
    StatusBar: {
      style: 'LIGHT',
      backgroundColor: '#FFFFFF',
      overlaysWebView: false,
    },
    Keyboard: {
      resize: 'native',
      resizeOnFullScreen: true,
    },
  },
};

export default config;
