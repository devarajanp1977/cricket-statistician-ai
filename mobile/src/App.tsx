import { useEffect } from 'react';
import { HashRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import { App as CapApp } from '@capacitor/app';
import { StatusBar, Style } from '@capacitor/status-bar';
import { SplashScreen } from '@capacitor/splash-screen';
import { AuthProvider, useAuth } from './lib/AuthContext';
import { ScreenChat } from './screens/Chat';
import { ScreenHistory } from './screens/History';
import { ScreenData } from './screens/Data';
import { ScreenKnowledge } from './screens/Knowledge';
import { ScreenOnboarding } from './screens/Onboarding';

function NativeShell() {
  const navigate = useNavigate();

  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return;
    StatusBar.setStyle({ style: Style.Light }).catch(() => undefined);
    StatusBar.setBackgroundColor({ color: '#FFFFFF' }).catch(() => undefined);
    SplashScreen.hide({ fadeOutDuration: 300 }).catch(() => undefined);

    const sub = CapApp.addListener('backButton', () => {
      if (window.history.length > 1) navigate(-1);
      else CapApp.exitApp();
    });
    return () => {
      sub.then((s) => s.remove()).catch(() => undefined);
    };
  }, [navigate]);

  return null;
}

function ProtectedShell() {
  // We allow guest mode but the chat works without auth too.
  return (
    <Routes>
      <Route path="/" element={<ScreenChat />} />
      <Route path="/chat/:sessionId" element={<ScreenChat />} />
      <Route path="/history" element={<ScreenHistory />} />
      <Route path="/data" element={<ScreenData />} />
      <Route path="/knowledge" element={<ScreenKnowledge />} />
      <Route path="/onboarding" element={<ScreenOnboarding />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function GatedRoutes() {
  const { loading } = useAuth();
  if (loading) {
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#FFFFFF',
        }}
      >
        <div className="skeleton" style={{ width: 80, height: 80, borderRadius: 20 }} />
      </div>
    );
  }
  return <ProtectedShell />;
}

export function App() {
  return (
    <div className="app-frame">
      <HashRouter>
        <AuthProvider>
          <NativeShell />
          <GatedRoutes />
        </AuthProvider>
      </HashRouter>
    </div>
  );
}
