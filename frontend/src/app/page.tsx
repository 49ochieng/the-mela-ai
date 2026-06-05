/**
 * Mela AI - Home Page
 * Landing page with authentication
 */

'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { InteractionStatus } from '@azure/msal-browser';
import { Button } from '@/components/ui/Button';
import { Loader2, MessageSquare, Shield, Zap, Brain } from 'lucide-react';
import Image from 'next/image';
import { loginRequest } from '@/lib/msal/config';

export default function Home() {
  const router = useRouter();
  const { instance, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const [msalError, setMsalError] = useState<string | null>(null);

  useEffect(() => {
    if (isAuthenticated) {
      router.push('/chat');
    }
  }, [isAuthenticated, router]);

  const handleLogin = async () => {
    setMsalError(null);
    try {
      await instance.loginPopup(loginRequest);
      router.push('/chat');
    } catch (error: any) {
      console.error('Login failed:', error);
      if (error?.errorCode && error.errorCode !== 'user_cancelled') {
        setMsalError(`Sign-in failed (${error.errorCode}): ${error.errorMessage}`);
      }
    }
  };

  if (inProgress === InteractionStatus.Login) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-primary/5 to-background">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-muted-foreground">Signing you in...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary/5 via-background to-secondary/10">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-background/80 backdrop-blur-md border-b">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-white border border-gray-200 flex items-center justify-center overflow-hidden">
                <Image
                  src="/mela-logo.png"
                  alt="Mela AI"
                  width={32}
                  height={32}
                  className="object-contain"
                />
              </div>
              <div>
                <h1 className="text-xl font-bold text-foreground">Mela AI</h1>
                <p className="text-xs text-muted-foreground">by Armely</p>
              </div>
            </div>
            <Button onClick={handleLogin} className="bg-primary hover:bg-primary/90">
              Sign in with Microsoft
            </Button>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <main className="pt-24">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20">
          <div className="text-center">
            <h2 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-foreground tracking-tight">
              Your Intelligent
              <span className="block text-primary">Enterprise Assistant</span>
            </h2>
            <p className="mt-6 text-lg sm:text-xl text-muted-foreground max-w-3xl mx-auto">
              Mela AI connects to your organization&apos;s knowledge, email, calendar, and tasks.
              Get instant answers, automate workflows, and boost productivity.
            </p>
            {msalError && (
              <p className="mt-4 text-sm text-red-500 max-w-lg mx-auto">{msalError}</p>
            )}
            <div className="mt-10 flex items-center justify-center">
              <Button
                size="lg"
                onClick={handleLogin}
                className="bg-primary hover:bg-primary/90 text-white px-8 py-6 text-lg"
              >
                Get Started
                <MessageSquare className="ml-2 h-5 w-5" />
              </Button>
            </div>
          </div>

          {/* Features Grid */}
          <div className="mt-24 grid grid-cols-1 md:grid-cols-3 gap-8">
            <FeatureCard
              icon={<Brain className="h-8 w-8" />}
              title="Intelligent Responses"
              description="Powered by advanced AI models with access to your organization's documents and knowledge base."
            />
            <FeatureCard
              icon={<Shield className="h-8 w-8" />}
              title="Enterprise Security"
              description="Your data never leaves your tenant. Built with Azure security best practices and compliance."
            />
            <FeatureCard
              icon={<Zap className="h-8 w-8" />}
              title="Workflow Automation"
              description="Send emails, schedule meetings, create tasks, and more - all through natural conversation."
            />
          </div>

          {/* Capabilities Section */}
          <div className="mt-24">
            <h3 className="text-2xl font-bold text-center mb-12">What Mela AI Can Do</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
              {[
                'Answer questions from documents',
                'Search SharePoint files',
                'Send and draft emails',
                'Schedule Teams meetings',
                'Create Planner tasks',
                'Check calendar availability',
                'Voice conversations',
                'Multi-model support',
              ].map((capability) => (
                <div
                  key={capability}
                  className="bg-card border rounded-lg p-4 text-center hover:border-primary/50 transition-colors"
                >
                  <p className="text-sm font-medium">{capability}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <footer className="border-t mt-24">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
              <p className="text-sm text-muted-foreground">
                Powered by <span className="font-semibold text-primary">Armely</span>
              </p>
              <p className="text-sm text-muted-foreground">
                © {new Date().getFullYear()} Armely. All rights reserved.
              </p>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="bg-card border rounded-xl p-6 hover:shadow-lg hover:border-primary/20 transition-all duration-300">
      <div className="w-14 h-14 rounded-lg bg-primary/10 flex items-center justify-center text-primary mb-4">
        {icon}
      </div>
      <h3 className="text-lg font-semibold mb-2">{title}</h3>
      <p className="text-muted-foreground">{description}</p>
    </div>
  );
}
