/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Test-only CPU endpoints; never copied into a Pi build. */
#include <command.h>
#include <env.h>
#include <mapmem.h>
#include <os.h>
#include <stdio.h>
#include <vsprintf.h>

int do_epk_test_reset(struct cmd_tbl *cmdtp, int flag, int argc, char *const argv[])
{
        puts("EPK_TEST_RESET\n");
        os_exit(0);
        return 0;
}

static int do_epk_test_booti(struct cmd_tbl *cmdtp, int flag, int argc,
                           char *const argv[])
{
        unsigned char *image, *dtb;
        const char *trial = env_get("epk_os_trial_started");
        const char *phase = env_get("epk_os_phase");

        if (argc != 4)
                return CMD_RET_USAGE;
        image = map_sysmem(hextoul(argv[1], NULL), 64);
        dtb = map_sysmem(hextoul(argv[3], NULL), 40);
        printf("EPK_TEST_LAUNCH image=%c dtb=%c trial=%s phase=%s args=%s\n",
               image[0], dtb[31], trial ? trial : "", phase ? phase : "",
               env_get("bootargs"));
        /* Successful CPU transfer never returns to the script. Inspect the
         * card at this point, before any later script command could save.
         */
        if (!env_get("test_return_kernel"))
                os_exit(0);
        return CMD_RET_FAILURE;
}
U_BOOT_CMD(booti, 4, 0, do_epk_test_booti, "test-only kernel endpoint", "");
