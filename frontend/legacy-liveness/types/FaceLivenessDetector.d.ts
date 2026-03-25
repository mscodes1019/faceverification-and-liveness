/**
 * Status of liveness detection result.
 * @readonly
 */
export enum LivenessStatus {
    RealFace = "RealFace",
    /** Liveness detection is done and the face was not determined to be a real face. */
    SpoofFace = "SpoofFace",
    /** Liveness detection is done and the result is only queryable from service. */
    ResultQueryableFromService = "ResultQueryableFromService",
}

/**
 * Status of recognition result.
 * @readonly
 */
export enum RecognitionStatus {
    Recognized = "Recognized",
    /** Recognition is done and a match was not made to the reference. */
    NotRecognized = "NotRecognized",
    /** Recognition is done and the result is only queryable from service. */
    ResultQueryableFromService = "ResultQueryableFromService",
}

/**
 * Result of recognition.
 */
export interface RecognitionResult {
     /**
     * Status of recognition.
     */
     readonly status: RecognitionStatus;
     /**
     * Confidence of the recognition result.
     * A logarithmic value between 0 and 1.
     * @see {@link https://go.microsoft.com/fwlink/?linkid=2243006 | Recognition Confidence Threshold to False Positive Rate mapping}
     */
    readonly matchConfidence: number;
}

/**
 * Final results from face analysis.
 */
export interface LivenessDetectionSuccess {
   /**
   * The result of liveness detection.
   */
  readonly livenessStatus: LivenessStatus;
  /**
   * The result of recognition.
   */
  readonly recognitionResult: RecognitionResult;
  /**
   * Result ID.
   */
  readonly resultId: string;
  /**
   * Digest.
   */
  readonly digest: string;
}

/**
 * Reason of liveness detection failure.
 * @readonly
 */
export enum LivenessError {
    /** Liveness has not failed. */
    None = "None",
    /** The operation took longer than the time limit. */
    TimedOut = "TimedOut",
    /** Invalid token. */
    InvalidToken = "InvalidToken",
    /** Camera permission issue. */
    CameraPermissionDenied = "CameraPermissionDenied",
    /** Other camera issues. */
    CameraStartupFailure = "CameraStartupFailure",    
    /** No face detected. */
    NoFaceDetected = "NoFaceDetected",    
    /** Tracking failure */
    FaceTrackingFailed = "FaceTrackingFailed",
    /** User did not smile during Active check. */
    SmileNotPerformed = "SmileNotPerformed",
    /** User did not perform the required head movements. */
    RandomPoseNotPerformed = "RandomPoseNotPerformed",
    /** API request timed out. */
    ServerRequestTimedOut = "ServerRequestTimedOut",
    /** Mouth region of the face was not visible.  */
    FaceMouthRegionNotVisible = "FaceMouthRegionNotVisible",
    /** Eye region of the face was not visible. */
    FaceEyeRegionNotVisible = "FaceEyeRegionNotVisible",
    /** Image was too blurry. */
    ExcessiveImageBlurDetected = "ExcessiveImageBlurDetected",
    /** Face was too brightly and unevenly illuminated. */
    ExcessiveFaceBrightness = "ExcessiveFaceBrightness",
    /** A mask was blocking the view of the face. */
    FaceWithMaskDetected = "FaceWithMaskDetected",
    /** Lighting condition during operation is not supported by current liveness detection mode. */
    EnvironmentNotSupported = "EnvironmentNotSupported",
    /** User cancelled */
    UserCancelledSession = "UserCancelledSession",
    /** User cancelled active motion */
    UserCancelledActiveMotion = "UserCancelledActiveMotion",
    /** Unexpected client error. */
    UnexpectedClientError = "UnexpectedClientError",
    /** Unexpected server error. */
    UnexpectedServerError = "UnexpectedServerError",
    /** Unexpected generic error. */
    Unexpected = "Unexpected"    
}

/**
 * Reason for recognition failure.
 * @readonly
 */
export enum RecognitionError {
    /** Recognition has not failed. */
    None = "None",
    /** Failure did not fall into any of the other categories. */
    GenericFailure = "GenericFailure",
    /** Face was looking away. */
    FaceNotFrontal = "FaceNotFrontal",
    /** Eye region of the face was not visible. */
    FaceEyeRegionNotVisible = "FaceEyeRegionNotVisible",
    /** Face was too brightly and unevenly illuminated. */
    ExcessiveFaceBrightness = "ExcessiveFaceBrightness",
    /** Image was too blurry. */
    ExcessiveImageBlurDetected = "ExcessiveImageBlurDetected",
    /** Face was not found in verify image */
    FaceNotFound = "FaceNotFound",
    /** Multiple face found in verify image */
    MultipleFaceFound = "MultipleFaceFound",
    /** Verify image has content decoding error */
    ContentDecodingError = "ContentDecodingError",
    /** Image size was too large */
    ImageSizeIsTooLarge = "ImageSizeIsTooLarge",
    /** Image size was too small */
    ImageSizeIsTooSmall = "ImageSizeIsTooSmall",
    /** Image had unsupported media type */
    UnsupportedMediaType = "UnsupportedMediaType",
    /** Mouth region of the face was not visible. */
    FaceMouthRegionNotVisible = "FaceMouthRegionNotVisible",
    /** Face with mask was detected. */
    FaceWithMaskDetected = "FaceWithMaskDetected",
}

/**
 * Failure results from face analysis.
 */
export interface LivenessDetectionError {
    /**
     * Reason for liveness detection failure.
     */
    readonly livenessError: LivenessError;
 
    /**
     * Reason for recognition failure.
     */
    readonly recognitionError: RecognitionError;
 }

/**
 * FaceLivenessDetector web component module.
 * @module FaceLivenessDetector
 * @exports FaceLivenessDetector
 * @extends HTMLElement
 */
export class FaceLivenessDetector extends HTMLElement {

  /**
   * Start the session for liveness.
   * @param {string} sessionAuthorizationToken - The token value.
   */
  start(sessionAuthorizationToken: string): Promise<LivenessDetectionSuccess>;

  /**
   * Set the locale to use for the session by using IETF BCP 47.
   * The default locale en.
   * Supported locales are en, pt, fa.
   * For other languages use the language property to set the dictionary.
   */
  locale : string;

  /**
   * Set the language property to set a new language dictionary.
   */
  languageDictionary : string;

  /**
   * Set the mediaInfoDeviceId to override the default camera used for face analysis. 
   * 
   */
  mediaInfoDeviceId : string;

  /**
   * Customize the default "Increase your screen brightness" image by providing your own image.
   */
  brightnessImagePath : string;

  /**
   * Customize the default font size for all the text.
   */
  fontSize : string;

  /**
   * Customize the default font family for all the text.
   */
  fontFamily : string;

  /**
   * Customize the default CSS styles for buttons.
   */
  buttonStyles : string;

  /**
   * Customize the default CSS styles for feedback messages.
   */
  feedbackMessageStyles : string;

  /**
   * Customize the session to skip the instructions for active motion part of the session.
   */
  skipInstructions : boolean;
}