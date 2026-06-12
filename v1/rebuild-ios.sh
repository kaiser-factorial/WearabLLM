#!/bin/bash
cd "$(dirname "$0")/phone-app"
echo "Installing JS dependencies..."
npm install
echo "Running Expo prebuild..."
npx expo prebuild --platform ios
echo "Installing CocoaPods..."
cd ios && pod install
echo "Done! Open ClaudeWearable.xcworkspace in Xcode to build."
