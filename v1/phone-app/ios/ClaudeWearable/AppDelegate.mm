#import "AppDelegate.h"

#import <React/RCTBundleURLProvider.h>
#import <React/RCTLinkingManager.h>
#import <objc/runtime.h>

// iOS 26 workaround: RCTAppearance.getColorScheme crashes because
// UIApplication.keyWindow is deprecated and returns nil on iOS 26,
// causing a JSI isObject() assertion failure. We swizzle it to return
// a hardcoded value before React Native ever calls it.
@interface RCTAppearanceFix : NSObject
@end
@implementation RCTAppearanceFix
+ (void)load {
  static dispatch_once_t onceToken;
  dispatch_once(&onceToken, ^{
    NSLog(@"🔧 [RCTAppearanceFix] +load fired");
    Class cls = NSClassFromString(@"RCTAppearance");
    if (!cls) {
      NSLog(@"🔧 [RCTAppearanceFix] ❌ RCTAppearance class NOT found");
      return;
    }
    SEL sel = @selector(getColorScheme);
    Method m = class_getInstanceMethod(cls, sel);
    if (!m) {
      NSLog(@"🔧 [RCTAppearanceFix] ❌ getColorScheme method NOT found");
      return;
    }
    NSLog(@"🔧 [RCTAppearanceFix] ✅ Swizzling getColorScheme");
    IMP patched = imp_implementationWithBlock(^NSString *(id _self) {
      return @"dark";
    });
    method_setImplementation(m, patched);
    NSLog(@"🔧 [RCTAppearanceFix] ✅ Swizzle complete");
  });
}
@end

@implementation AppDelegate

- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions
{
  self.moduleName = @"main";

  // You can add your custom initial props in the dictionary below.
  // They will be passed down to the ViewController used by React Native.
  self.initialProps = @{};

  return [super application:application didFinishLaunchingWithOptions:launchOptions];
}

- (NSURL *)sourceURLForBridge:(RCTBridge *)bridge
{
  return [self bundleURL];
}

- (NSURL *)bundleURL
{
#if DEBUG
  return [[RCTBundleURLProvider sharedSettings] jsBundleURLForBundleRoot:@".expo/.virtual-metro-entry"];
#else
  return [[NSBundle mainBundle] URLForResource:@"main" withExtension:@"jsbundle"];
#endif
}

// Linking API
- (BOOL)application:(UIApplication *)application openURL:(NSURL *)url options:(NSDictionary<UIApplicationOpenURLOptionsKey,id> *)options {
  return [super application:application openURL:url options:options] || [RCTLinkingManager application:application openURL:url options:options];
}

// Universal Links
- (BOOL)application:(UIApplication *)application continueUserActivity:(nonnull NSUserActivity *)userActivity restorationHandler:(nonnull void (^)(NSArray<id<UIUserActivityRestoring>> * _Nullable))restorationHandler {
  BOOL result = [RCTLinkingManager application:application continueUserActivity:userActivity restorationHandler:restorationHandler];
  return [super application:application continueUserActivity:userActivity restorationHandler:restorationHandler] || result;
}

// Explicitly define remote notification delegates to ensure compatibility with some third-party libraries
- (void)application:(UIApplication *)application didRegisterForRemoteNotificationsWithDeviceToken:(NSData *)deviceToken
{
  return [super application:application didRegisterForRemoteNotificationsWithDeviceToken:deviceToken];
}

// Explicitly define remote notification delegates to ensure compatibility with some third-party libraries
- (void)application:(UIApplication *)application didFailToRegisterForRemoteNotificationsWithError:(NSError *)error
{
  return [super application:application didFailToRegisterForRemoteNotificationsWithError:error];
}

// Explicitly define remote notification delegates to ensure compatibility with some third-party libraries
- (void)application:(UIApplication *)application didReceiveRemoteNotification:(NSDictionary *)userInfo fetchCompletionHandler:(void (^)(UIBackgroundFetchResult))completionHandler
{
  return [super application:application didReceiveRemoteNotification:userInfo fetchCompletionHandler:completionHandler];
}

@end
