(function() {
  "use strict";
  AMFA.hasDeviceKey().then(function(bound) {
    UI.postToParent({
      type: "amfa:device",
      deviceId: AMFA.deviceId(),
      keyBound: bound
    });
  });
})();
