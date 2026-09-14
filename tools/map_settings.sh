# Single place that maps a map-mode name to xcodebuild build settings.
#
# Both the probe step and the build step source this, so the settings that were
# *measured* as usable are byte-for-byte the settings that get *built* with.
# Keeping two copies in two steps is how a probe ends up blessing a mode the
# build never actually used.
#
# Note the single quotes on per_target: `$(TARGET_TEMP_DIR)` must reach
# xcodebuild as a literal build-setting reference.  Double quotes would make
# bash run command substitution on it.

MAP_BASENAME="rra_link.map"

map_settings_for() {
  case "$1" in
    default)    MAP_SETTINGS=( LD_GENERATE_MAP_FILE=YES ) ;;
    per_target) MAP_SETTINGS=( LD_GENERATE_MAP_FILE=YES
                               'LD_MAP_FILE_PATH=$(TARGET_TEMP_DIR)/'"$MAP_BASENAME" ) ;;
    global)     MAP_SETTINGS=( LD_GENERATE_MAP_FILE=YES
                               "LD_MAP_FILE_PATH=$RUNNER_TEMP/out/$MAP_BASENAME" ) ;;
    none)       MAP_SETTINGS=( ) ;;
    *) echo "未知 map mode: $1" >&2; return 2 ;;
  esac
}
