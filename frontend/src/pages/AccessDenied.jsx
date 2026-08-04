// src/pages/AccessDenied.jsx 
import React from "react";

const AccessDenied = () => {
  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-purple-900 to-gray-900 text-white flex items-center justify-center p-4">
      <div className="max-w-md w-full text-center">
        {/* Logo */}
        <div className="mb-8">
          <div className="w-20 h-20 bg-gradient-to-r from-green-400 to-blue-500 rounded-full flex items-center justify-center mx-auto mb-4">
            <img
              src="/server-logo.png"
              alt="Server Logo"
              className="w-12 h-12 rounded-full"
              onError={(e) => {
                e.target.src =
                  'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="white" d="M12 2L13.09 8.26L22 9L13.09 15.74L12 22L10.91 15.74L2 9L10.91 8.26L12 2Z"/></svg>';
              }}
            />
          </div>
          <h1 className="text-2xl font-bold text-white">FUT Trading Dashboard</h1>
        </div>

        {/* Main Content */}
        <div className="bg-gray-800/40 backdrop-blur-sm rounded-2xl p-8 border border-gray-700/50">
          <div className="mb-6">
            <div className="w-16 h-16 bg-red-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg
                className="w-8 h-8 text-red-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.732 16.5c-.77.833.192 2.5 1.732 2.5z"
                />
              </svg>
            </div>

            <h2 className="text-xl font-semibold mb-4">Access Restricted</h2>

            <p className="text-gray-300 mb-6 leading-relaxed">
              We're sorry, but access to this dashboard is limited to members of
              our Discord server. This helps us maintain a secure trading
              community for our members.
            </p>

            <div className="bg-gray-900/50 rounded-lg p-4 mb-6">
              <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-2">
                To gain access:
              </h3>
              <ol className="text-left text-sm text-gray-300 space-y-2">
                <li className="flex items-start space-x-2">
                  <span className="text-purple-400 font-semibold">1.</span>
                  <span>Join our Discord server using the link below</span>
                </li>
                <li className="flex items-start space-x-2">
                  <span className="text-purple-400 font-semibold">2.</span>
                  <span>Verify your account and read the server rules</span>
                </li>
                <li className="flex items-start space-x-2">
                  <span className="text-purple-400 font-semibold">3.</span>
                  <span>Return here and log in again with Discord</span>
                </li>
              </ol>
            </div>

            {/* Join Server Button */}
            <a
              href="https://discord.gg/3sSpu3rDgn"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center space-x-2 bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-3 px-6 rounded-lg transition-colors mb-4"
            >
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419-.0002 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9554 2.4189-2.1568 2.4189Z" />
              </svg>
              <span>Join Our Discord Server</span>
            </a>

            <p className="text-xs text-gray-400">
              Already a member? Try logging out and back in again.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-6 text-center">
          <p className="text-xs text-gray-500">
            Having trouble? Contact us on Discord for support.
          </p>
        </div>
      </div>
    </div>
  );
};

export default AccessDenied;