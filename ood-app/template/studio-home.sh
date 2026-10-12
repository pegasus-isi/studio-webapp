# Studio's own home directory for an Open OnDemand session (#482).
#
# Sourced by script.sh, which OOD stages beside this file. It is plain shell
# rather than ERB so tests/test_ood_studio_home.py can run the real thing.
#
# Why Studio gets its own home: everything it writes is $HOME-relative (its
# database, the marketplace checkout, ~/.pegasusrc, the generated CLAUDE.md and
# AGENTS.md, and the configs its tool adapters write for codex, opencode and
# claude). Under KubeSpawner the home is a volume that exists only for Studio.
# On a cluster it is the user's real home, and a first launch would overwrite
# their own setup of those very tools. The container is therefore started with
# `apptainer --home "$STUDIO_HOME"`, and this file decides, checks and
# prepares that directory.

# studio_home_resolve REQUESTED DEFAULT REAL_HOME
#
# Prints the absolute Studio home: REQUESTED if given, else DEFAULT. A leading
# `~` is expanded against REAL_HOME, since that is how people type paths into a
# form. Fails, with the reason on stderr, for anything that would put Studio's
# files back among the user's own:
#   - a relative path (it would resolve against the job's output directory);
#   - the real home itself, which is exactly what this exists to avoid;
#   - an ancestor of the real home (/home, /): Studio would scan and write
#     across other users' directories, and treat the real home as a subfolder.
studio_home_resolve() {
    local requested="$1" default="$2" real_home="$3" dir
    dir="${requested:-${default}}"
    # The quotes are the point: this matches the literal `~` a person typed,
    # which the form hands over unexpanded.
    # shellcheck disable=SC2088
    case "${dir}" in
        "~") dir="${real_home}" ;;
        "~/"*) dir="${real_home}/${dir#"~/"}" ;;
    esac
    case "${dir}" in
        /?*) ;;
        *)
            echo "Studio directory must be an absolute path, got '${dir}'." >&2
            return 1
            ;;
    esac
    # Normalise without requiring it to exist yet: strip trailing slashes and
    # collapse `//`, so "/home/u/" compares equal to "/home/u" below.
    while [ "${dir%/}" != "${dir}" ] && [ "${dir}" != "/" ]; do dir="${dir%/}"; done
    while case "${dir}" in *//*) true ;; *) false ;; esac; do dir="${dir//\/\//\/}"; done
    # No `.` or `..` components. The checks below compare strings for the parts
    # of the path that do not exist yet, and `/home/u/new/..` names the real
    # home only once `mkdir -p` has created `new`: it passed every comparison
    # and then became the home it was meant to keep out of.
    case "/${dir}/" in
        */./*|*/../*)
            echo "Studio directory may not contain '.' or '..' components," \
                 "got '${dir}'." >&2
            return 1
            ;;
    esac
    local real="${real_home%/}"
    # Compare physical paths too, so a symlink to the home (or to a directory
    # containing it) is caught. Resolve the deepest ancestor that exists and
    # append the rest: a symlink anywhere above a not-yet-created leaf
    # (`~/link/studio` with link -> /) is just as much a way back in.
    local phys_real phys_dir head="${dir}" tail=""
    phys_real="$(cd "${real}" 2>/dev/null && pwd -P || echo "${real}")"
    while [ -n "${head}" ] && [ ! -d "${head}" ]; do
        tail="/${head##*/}${tail}"
        head="${head%/*}"
    done
    phys_dir="$(cd "${head:-/}" 2>/dev/null && pwd -P || echo "${head:-/}")"
    phys_dir="${phys_dir%/}${tail}"
    phys_dir="${phys_dir:-/}"
    local d r
    for d in "${dir}" "${phys_dir}"; do
        for r in "${real}" "${phys_real}"; do
            if [ "${d}" = "${r}" ]; then
                echo "Studio directory cannot be your home directory itself" \
                     "(${real}); Studio would overwrite your own tool" \
                     "configuration there. Choose a subdirectory such as" \
                     "${real}/pegasus-studio." >&2
                return 1
            fi
            if [ "${d}" = "/" ] || case "${r}/" in "${d}/"*) true ;; *) false ;; esac; then
                echo "Studio directory '${dir}' contains your home directory;" \
                     "choose a directory inside it or elsewhere." >&2
                return 1
            fi
        done
    done
    printf '%s\n' "${dir}"
}

# studio_home_prepare STUDIO_HOME REAL_HOME INHERIT
#
# Creates STUDIO_HOME (0700: it will hold an IDTOKEN link, API keys in the
# Studio database and the OpenCode password) and links in the entries of the
# whitespace-separated INHERIT list, relative to both homes. A link is made
# only when the entry exists in the real home and nothing at all (not even a
# dangling link) is at the target: a user who replaced one with their own copy
# keeps it, and a second session never rewrites anything.
#
# This is what keeps a cluster account's own credentials working inside the
# isolated home: an HTCondor IDTOKEN in ~/.condor/tokens.d, git's identity in
# ~/.gitconfig. They are links rather than copies so a refreshed token is
# picked up without relaunching.
studio_home_prepare() {
    local home="$1" real_home="$2" inherit="$3" item src dst
    mkdir -p "${home}" || return 1
    chmod 700 "${home}" 2>/dev/null || true
    for item in ${inherit}; do
        item="${item#/}"
        case "${item}" in
            ""|*..*) echo "Skipping inherit entry '${item}'." >&2; continue ;;
        esac
        src="${real_home%/}/${item}"
        dst="${home}/${item}"
        [ -e "${src}" ] || continue
        [ -e "${dst}" ] || [ -L "${dst}" ] && continue
        mkdir -p "$(dirname "${dst}")" && ln -s "${src}" "${dst}" \
            && echo "Linked ~/${item} into the Studio directory."
    done
    return 0
}
