#!/usr/bin/env bash
# Reuse the classpath correction already verified on the course VM.
# Keep this profile local to R3; do not edit /etc/spark/conf.
. /etc/spark/conf/spark-env.sh
r3_old_ifs=$IFS
IFS=:
read -ra r3_cp_entries <<< "$SPARK_DIST_CLASSPATH"
IFS=$r3_old_ifs
r3_filtered=()
for r3_entry in "${r3_cp_entries[@]}"; do
    case "$r3_entry" in
        '/usr/lib/hive/lib/*'|'/usr/lib/flume-ng/lib/*')
            for r3_jar in "${r3_entry%/*}"/*.jar; do
                case "$r3_jar" in
                    /usr/lib/hive/lib/hive-jdbc-standalone.jar|/usr/lib/flume-ng/lib/derby-*.jar) continue ;;
                esac
                r3_filtered+=("$r3_jar")
            done
            ;;
        *) r3_filtered+=("$r3_entry") ;;
    esac
done
export SPARK_DIST_CLASSPATH="$(IFS=:; printf '%s' "${r3_filtered[*]}")"
