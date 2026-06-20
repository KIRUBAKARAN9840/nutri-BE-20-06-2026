# =============================================================================
# Fittbot Models Package
# Re-exports all models so existing imports continue to work:
#   from app.models.fittbot_models import Gym, Client, ...
# =============================================================================

# --- Gym & Gym Infrastructure ---
from app.models.fittbot_models.gym import (
    Gym, GymOwner, GymLocation, GymDetails, GymBatches, GymPlans,
    GymMembershipOffer, GymFees,
    LiveCount, NewOffer, NoCostEmi, AccountDetails, AccountDetailsEditRequest,
    GymPhoto, GymStudiosPic, GymImportData, GymManualData, GymEnquiry,
    GymAnnouncement, GymOffer, GymStudiosRequest, Brochures, GymOnboardingPics,
    BiometricModal, FittbotAssociates, Expenditure, GymAnalysis, GymMonthlyData,
    FittbotGymMembership,
)

# --- Client & Client Data ---
from app.models.fittbot_models.client import (
    Client, ClientGym, OldGymData, ClientTarget, ClientActual,
    ClientActualAggregatedWeekly, ClientActualAggregated, ClientGeneralAnalysis,
    ClientScheduler, ClientFittbotAccess, ClientBirthday, ClientWeightData,
    WeightJourney, ClientWeightSelection, WeightManagementPlan, ClientNextXp,
    VoicePreference, Preference, SmartWatch, ClientModalTracker, CalorieEvent,
    FittbotPlans, ClientCharacter,
)

# --- Trainer ---
from app.models.fittbot_models.trainer import (
    Trainer, TrainerProfile, TrainerAttendance,
)

# --- Attendance & Hourly Aggregations ---
from app.models.fittbot_models.attendance import (
    Attendance, AttendanceGym, AttendanceStreak, GymHourlyAgg, DailyGymHourlyAgg,
)

# --- Food & Nutrition ---
from app.models.fittbot_models.food_nutrition import (
    Food, CustomFood, DietTemplate, ActualDiet, FittbotDietTemplate,
    ClientDietTemplate, TemplateDiet, IndianFoodMaster,
)


# --- Workout ---
from app.models.fittbot_models.workout import (
    WorkoutTemplate, ActualWorkout, FittbotWorkout, ClientWorkoutTemplate,
    TemplateWorkout, DefaultWorkoutTemplates, HomeWorkout, EquipmentWorkout,
    QRCode, FittbotMuscleGroup, MuscleAggregatedInsights, AggregatedInsights,
    ClientWeeklyPerformance,
)

# --- Social / Feed ---
from app.models.fittbot_models.social import (
    Post, PostMedia, Comment, Like, Report, BlockedUsers, FeedInterest,
)

# --- Messaging & Notifications ---
from app.models.fittbot_models.messaging import (
    Message, Notification, FcmToken, Reminder, GBMessage, New_Session,
    Participant, JoinProposal, RejectedProposal,
)

# --- Rewards & Gamification ---
from app.models.fittbot_models.rewards import (
    RewardQuest, RewardGym, RewardClientHistory, LeaderboardDaily,
    LeaderboardMonthly, LeaderboardOverall, RewardBadge, RewardPrizeHistory,
    RewardInterest, RewardProgramOptIn, RewardProgramEntry,
)

# --- Payments & Billing ---
from app.models.fittbot_models.payments import (
    RazorpayOrder, RazorpayPayment, FeeHistory, FeesReceipt, EnquiryEstimates,
    EstimateDiscount, AboutToExpire, GymBusinessPayment,
)

# --- Sessions & Bookings ---
from app.models.fittbot_models.sessions import (
    SESSION_SCHEMA, ClassSession, GymSession, SessionSetting, SessionSchedule,
    SessionBooking, SessionPurchase, SessionBookingDay, SessionBookingAudit,
    SessionQrCode,
)

# --- Referral System ---
from app.models.fittbot_models.referral import (
    ReferralCode, ReferralMapping, ReferralRedeem, ReferralFittbotCash,
    ReferralFittbotCashLogs, ReferralGymCode, ReferralGymCash,
    ReferralGymCashLogs, ReferralGymMapping,
)

# --- Support, Feedback & Ratings ---
from app.models.fittbot_models.support import (
    Gym_Feedback, Feedback, ClientToken, OwnerToken, ClientFeedback,
    FittbotRatings, FreeTrial, DeleteRequest, OwnerDeleteRequest,
)

# --- Onboarding & Agreements ---
from app.models.fittbot_models.onboarding import (
    GymVerificationDocument, GymPrefilledAgreement, GymAgreementSteps,
    GymOnboardingEsign, GymAgreement,
)

# --- Manual / CRM Clients ---
from app.models.fittbot_models.manual_clients import (
    ManualClient, ManualAttendance, ManualFeeHistory, ImportClientAttendance,
)

# --- Auth / Audit ---
from app.models.fittbot_models.auth_events import AuthEvent

# --- Marketing / Attribution ---
from app.models.fittbot_models.ad_registration import AdRegistration

# --- Miscellaneous ---
from app.models.fittbot_models.misc import (
    Avatar, HomePoster, ManualPoster, OwnerHomePoster, AppVersion, AppRedirect,
    CharactersCombinationOld, CharactersCombination, FittbotCharacters, AppOpen,
    ActiveUser, AIConsent, AIReports, StepConsent, Royalty, RoyaltyStatus,
    OwnerModalTracker, GymJoinRequest,
)

# --- GymMate / Fymble Social (schema: gym_mate) ---
from app.models.fittbot_models.gymmate import (
    GYMMATE_SCHEMA,
    PRIMARY_GOAL_VALUES,
    PREFERRED_TIMING_VALUES,
    GYM_PERSONALITY_VALUES,
    FRIEND_REQUEST_STATUSES,
    STORY_MEDIA_TYPES,
    STORY_AUDIENCES,
    MATE_PREFERENCE_VALUES,
    FITNESS_LEVEL_VALUES,
    WORKOUT_VIBE_VALUES,
    SESSION_STATUSES,
    SESSION_PAYMENT_STATUSES,
    SESSION_PAYMENT_MODES,
    GymMateProfile,
    GymMateProfilePhoto,
    GymMateFriendRequest,
    GymMateFriendship,
    GymMateStory,
    GymMateStoryView,
    GymMateSession,
    GymMateSessionRequest,
    GymMateSessionMember,
    GymMateBlock,
    GymMateReport,
    REPORT_ENTITY_TYPES,
    REPORT_REASONS,
    REPORT_STATUSES,
    SESSION_REQUEST_STATUSES,
)
